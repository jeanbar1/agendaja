"""
O webhook do WhatsApp, a tela de mensagens e a tela de conexao.

O webhook e o que fecha o ciclo do sistema. Ate aqui as mensagens so
saiam; com ele o sistema passa a escutar tambem. Quando o cliente
responde CONFIRMAR ou CANCELAR no WhatsApp dele, chega um evento aqui e
o agendamento se atualiza sozinho - o profissional nao precisa fazer
nada.

Pra mim essa e a parte que diferencia o sistema de uma agenda comum. Nao
e so avisar: e receber a resposta e agir em cima dela.

Quem manda esses eventos e a Evolution API. O endereco que eu cadastro
la no contêiner e /webhook/whatsapp/, e ela chama esse endereco a cada
mensagem recebida e a cada aviso de entrega.
"""

import json
import logging

from django.conf import settings
from django.contrib import messages as avisos
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from agenda.models import Agendamento
from contas.views import obter_profissional

from . import envio, mensagens
from .canais import CanalEvolution, ErroEnvio, obter_canal
from .models import Notificacao

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["POST"])
def webhook(request):
    """Recebe os eventos que a Evolution manda.

    O csrf_exempt e obrigatorio aqui. Quem chama e o contêiner, nao um
    formulario do meu site, entao nao tem token CSRF pra mandar.

    Em troca eu confiro um token meu na URL, quando esta configurado.
    Sem isso, qualquer pessoa que descobrisse o endereco poderia mandar
    um POST cancelando agendamento dos outros.
    """
    esperado = getattr(settings, "WEBHOOK_TOKEN", "")
    if esperado and request.GET.get("token") != esperado:
        return HttpResponseForbidden("Token inválido.")

    try:
        dados = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"erro": "corpo inválido"}, status=400)

    # Registro o evento cru no log. A Evolution muda o formato entre
    # versoes, entao quando alguma coisa nao funciona eu preciso ver o
    # que chegou de verdade - sem isso fico adivinhando.
    logger.debug("Webhook recebido: %s", json.dumps(dados)[:1000])

    try:
        _processar_evento(dados)
    except Exception as erro:
        # Engulo o erro de proposito e mesmo assim respondo 200.
        #
        # Parece errado, mas e o contrario: se eu devolvesse erro, a
        # Evolution ia entender que nao recebi e ficaria reenviando o
        # mesmo evento sem parar. Melhor registrar no log e responder
        # que recebi - o problema eu vejo depois, com calma.
        logger.exception("Erro ao processar webhook: %s", erro)

    return HttpResponse("OK", status=200)


def _processar_evento(dados):
    """Separa os dois tipos de evento que me interessam.

    A Evolution manda o nome do evento em "event" e o conteudo em
    "data". Dos varios que ela pode mandar, eu uso dois:

    - messages.upsert: chegou mensagem nova de alguem;
    - messages.update: mudou o estado de uma mensagem que eu mandei
      (chegou no celular, foi lida).

    O "instance" diz de qual sessao de WhatsApp veio, e e por ele que eu
    sei de qual profissional e a conversa.
    """
    evento = (dados.get("event") or "").replace("_", ".").lower()
    conteudo = dados.get("data")
    instancia = dados.get("instance") or ""

    if conteudo is None:
        return

    # Em alguns eventos o "data" vem como lista de varios. Trato sempre
    # como lista pra nao precisar de dois caminhos.
    itens = conteudo if isinstance(conteudo, list) else [conteudo]

    for item in itens:
        if not isinstance(item, dict):
            continue
        if evento == "messages.upsert":
            _tratar_mensagem(item, instancia)
        elif evento == "messages.update":
            _atualizar_entrega(item)


def _atualizar_entrega(item):
    """Anota que a mensagem chegou no celular ou foi lida."""
    chave = item.get("key") or {}
    id_mensagem = item.get("keyId") or chave.get("id") or ""
    if not id_mensagem:
        return

    # Traduzo o nome que a Evolution usa pro nome que eu uso no modelo.
    # DELIVERY_ACK e "chegou no aparelho", READ e "abriu a conversa".
    equivalencia = {
        "DELIVERY_ACK": Notificacao.Situacao.ENTREGUE,
        "DELIVERED": Notificacao.Situacao.ENTREGUE,
        "READ": Notificacao.Situacao.LIDA,
        "PLAYED": Notificacao.Situacao.LIDA,
        "ERROR": Notificacao.Situacao.FALHOU,
    }
    nova = equivalencia.get(str(item.get("status") or "").upper())
    if nova is None:
        # Chega tambem SERVER_ACK, que e so "o servidor recebeu" - isso
        # eu ja marquei sozinho na hora do envio. Ignoro pra nao andar
        # pra tras na escada de situacoes.
        return

    # Uso update() no queryset em vez de buscar o objeto e salvar: e um
    # UPDATE so, e nao dispara os sinais do Django - que aqui so
    # atrapalhariam.
    Notificacao.objects.filter(id_mensagem=id_mensagem).update(situacao=nova)


def _telefone_do_evento(item):
    """Tira o telefone de dentro do evento, no formato que eu guardo.

    A Evolution identifica a conversa pelo remoteJid, que vem assim:
    5577999998888@s.whatsapp.net. Eu guardo +5577999998888, entao pego
    so o que vem antes do @ e ponho o + na frente.

    Conversa de grupo termina em @g.us. Devolvo vazio nesses casos: nao
    faz sentido confirmar agendamento por mensagem de grupo.
    """
    jid = (item.get("key") or {}).get("remoteJid") or ""
    if not jid or "@g.us" in jid:
        return ""

    digitos = "".join(c for c in jid.split("@")[0] if c.isdigit())
    return f"+{digitos}" if digitos else ""


def _texto_do_evento(item):
    """Tira o texto escrito de dentro do evento.

    A Evolution embrulha o texto em lugares diferentes conforme o tipo
    da mensagem: "conversation" quando a pessoa so digitou, e
    "extendedTextMessage" quando ela respondeu citando a mensagem
    anterior - que e justamente o que acontece quando ela responde o
    lembrete. Preciso aceitar os dois.
    """
    mensagem = item.get("message") or {}
    if not isinstance(mensagem, dict):
        return ""

    caminhos = (
        mensagem.get("conversation"),
        (mensagem.get("extendedTextMessage") or {}).get("text"),
        (mensagem.get("buttonsResponseMessage") or {}).get("selectedDisplayText"),
        (mensagem.get("templateButtonReplyMessage") or {}).get("selectedDisplayText"),
        (mensagem.get("listResponseMessage") or {}).get("title"),
    )
    for texto in caminhos:
        if texto:
            return str(texto)
    return ""


def _tratar_mensagem(item, instancia):
    # fromMe e a mensagem que o proprio sistema mandou, voltando como
    # eco. Sem esse corte, o sistema leria as proprias mensagens e
    # reagiria a elas - o lembrete diz "responda CANCELAR", entao ele
    # cancelaria o agendamento sozinho.
    if (item.get("key") or {}).get("fromMe"):
        return

    telefone = _telefone_do_evento(item)
    if not telefone:
        return

    acao = _identificar_acao(_texto_do_evento(item))
    if acao is None:
        # Cliente mandou "obrigado", figurinha, audio... Nao respondo
        # nada. Prefiro ficar quieto a mandar "nao entendi" e virar
        # aquele robo chato.
        return

    if acao == "descadastrar":
        # Sair nao depende de ter agendamento, entao trato antes.
        _descadastrar(telefone, instancia)
        return

    agendamento = _agendamento_mais_proximo(telefone, instancia)
    if agendamento is None:
        logger.info("Resposta de %s sem agendamento ativo.", telefone)
        return

    if acao == "confirmar":
        agendamento.status = Agendamento.Status.CONFIRMADO
        agendamento.confirmado_em = timezone.now()
        agendamento.save(update_fields=["status", "confirmado_em"])
        resposta = (
            f"Presença confirmada para {agendamento.inicio:%d/%m às %H:%M}. "
            "Até lá!"
        )
    else:
        agendamento.status = Agendamento.Status.CANCELADO
        agendamento.cancelado_em = timezone.now()
        agendamento.motivo_cancelamento = "Cancelado pelo cliente via WhatsApp"
        agendamento.save(
            update_fields=["status", "cancelado_em", "motivo_cancelamento"]
        )
        # Aviso o profissional que abriu vaga. Derrubar o lembrete que
        # ainda ia sair e avisar o cliente do cancelamento quem faz e o
        # sinal, no save acima - se eu repetisse aqui, o meu
        # cancelar_pendentes derrubaria justamente o aviso de
        # cancelamento que o sinal acabou de criar.
        envio.avisar_cancelamento_pelo_cliente(agendamento)

        # Nao respondo nada aqui: a mensagem de cancelamento ja esta
        # programada pelo sinal e sai sozinha. Duas mensagens dizendo a
        # mesma coisa seria ruim.
        resposta = ""

    if not resposta:
        return

    # Respondo pela sessao do profissional do agendamento. O cliente
    # mandou mensagem pro numero dele, entao a resposta tem que voltar
    # do mesmo numero - se saisse de outro, ia parecer golpe.
    canal = obter_canal(agendamento.profissional)
    if canal is not None:
        canal.enviar(telefone, resposta)


def _identificar_acao(texto):
    """Descobre o que o cliente quis dizer.

    Comparo em minusculo e sem espaco sobrando porque no celular ninguem
    escreve com cuidado. E aceito tanto a palavra solta ("sim") quanto a
    frase que contem ela ("quero confirmar sim").
    """
    texto = (texto or "").strip().lower()
    if not texto:
        return None

    # A ordem importa. "sair" vem primeiro porque descadastrar e pedido
    # mais forte que confirmar ou cancelar.
    if any(p in texto for p in mensagens.PALAVRAS_DESCADASTRO):
        return "descadastrar"
    if "confirmar" in texto or texto in mensagens.PALAVRAS_CONFIRMAR:
        return "confirmar"
    if "cancelar" in texto or texto in mensagens.PALAVRAS_CANCELAR:
        return "cancelar"
    return None


def _profissional_da_instancia(instancia):
    """Descobre de quem e a sessao que recebeu a mensagem."""
    if not instancia:
        return None

    # Import aqui dentro pra nao criar dependencia fixa entre os apps
    # por causa de uma consulta so.
    from contas.models import Profissional

    return Profissional.objects.filter(instancia_whatsapp=instancia).first()


def _agendamento_mais_proximo(telefone, instancia=""):
    """O proximo agendamento dessa pessoa, naquela sessao de WhatsApp.

    O cliente responde so "confirmar", sem dizer de qual agendamento.
    Entao eu chuto o mais logico: o proximo que ainda vai acontecer.

    Filtrar pelo profissional da sessao nao e detalhe. A mesma pessoa
    pode ter horario marcado com o barbeiro e com a tatuadora; se eu
    olhasse so o telefone, ela responderia o lembrete do barbeiro e
    confirmaria o da tatuadora - o que chegasse primeiro na ordem.
    """
    consulta = Agendamento.objects.filter(
        cliente__telefone=telefone,
        status__in=Agendamento.STATUS_OCUPAM_AGENDA,
        inicio__gte=timezone.now(),
    )

    profissional = _profissional_da_instancia(instancia)
    if profissional is not None:
        consulta = consulta.filter(profissional=profissional)

    return (
        consulta.select_related("cliente", "servico", "profissional")
        .order_by("inicio")
        .first()
    )


def _descadastrar(telefone, instancia=""):
    """Revoga o consentimento quando o cliente pede pra sair.

    Isso aqui e obrigacao, nao cortesia: a LGPD garante que a pessoa
    possa cancelar o consentimento a qualquer hora.

    Repare que eu percorro TODOS os cadastros com esse telefone. Como o
    cliente pode estar cadastrado em varios profissionais, quando ele
    escreve SAIR ele quer parar de receber tudo - nao so daquele que
    mandou a ultima mensagem.
    """
    # Import aqui dentro pra nao criar dependencia fixa entre os apps
    # por causa de uma funcao so.
    from clientes.models import Cliente

    for cliente in Cliente.objects.filter(telefone=telefone):
        for consentimento in cliente.consentimentos.filter(revogado_em__isnull=True):
            consentimento.revogar()

    # Confirmo o descadastro. Pode parecer contraditorio mandar mensagem
    # pra quem pediu pra nao receber mais, mas essa e a ultima - e sem
    # ela a pessoa fica sem saber se funcionou.
    #
    # Respondo pela sessao que recebeu o pedido. Se eu nao souber qual
    # e, fico calado: o descadastro acima ja valeu, e mandar pelo numero
    # de outro profissional seria pior que nao mandar.
    profissional = _profissional_da_instancia(instancia)
    if profissional is None:
        return

    canal = obter_canal(profissional)
    if canal is not None:
        canal.enviar(
            telefone,
            "Pronto, você não vai mais receber mensagens automáticas. "
            "Seus agendamentos continuam valendo.",
        )


# ---------------------------------------------------------------------------
# Histórico das mensagens
# ---------------------------------------------------------------------------

@login_required
def historico(request):
    """A tela onde o profissional acompanha as mensagens.

    Botei essa tela porque notificacao que some e pior que notificacao
    que nao existe: se o cliente disser que nao recebeu, o profissional
    precisa conseguir olhar e ver o que aconteceu.
    """
    profissional = obter_profissional(request)
    if profissional is None:
        return render(request, "painel/sem_perfil.html")

    situacao = request.GET.get("situacao", "")

    # Filtro pelo profissional atravessando o agendamento. A notificacao
    # nao tem ligacao direta com o profissional, entao vou pelo caminho
    # agendamento__profissional.
    consulta = Notificacao.objects.filter(
        agendamento__profissional=profissional
    ).select_related("agendamento", "agendamento__cliente", "agendamento__servico")

    if situacao:
        consulta = consulta.filter(situacao=situacao)

    # Materializo em lista porque logo abaixo eu preciso pendurar um
    # atributo em cada objeto, e isso nao funciona direto no queryset.
    notificacoes = list(consulta[:100])

    # Pra tudo que ainda nao saiu eu ja monto o link de envio manual.
    # E a saida garantida: se a Evolution estiver fora do ar, o
    # profissional clica no link e manda pelo WhatsApp dele mesmo.
    manuais = {Notificacao.Situacao.PENDENTE, Notificacao.Situacao.FALHOU}
    for n in notificacoes:
        if n.situacao in manuais:
            n.link_manual = envio.link_manual(n)

    pendentes = Notificacao.objects.filter(
        agendamento__profissional=profissional,
        situacao=Notificacao.Situacao.PENDENTE,
    )

    contexto = {
        "profissional": profissional,
        "notificacoes": notificacoes,
        "situacao": situacao,
        "situacoes": Notificacao.Situacao.choices,
        # Contador de pendentes: se esse numero so cresce, e sinal de
        # que o comando agendado parou de rodar.
        "pendentes": pendentes.count(),
        # Atrasadas sao as que ja passaram da hora e nao sairam. Uso
        # isso pro aviso no topo da tela em vez de perguntar a Evolution
        # se esta conectada: consultar o contêiner a cada visita
        # deixaria a pagina lenta, e o numero de atrasadas conta a mesma
        # historia - se tem mensagem vencida parada, alguma coisa no
        # caminho parou.
        "atrasadas": pendentes.filter(agendada_para__lte=timezone.now()).count(),
    }
    return render(request, "notificacoes/historico.html", contexto)


# ---------------------------------------------------------------------------
# Conexão do WhatsApp pelo navegador
# ---------------------------------------------------------------------------
#
# Antes, conectar o numero exigia abrir o terminal e rodar
# `manage.py whatsapp <slug> --conectar`. Funciona, mas so pra quem sabe
# mexer em terminal - e o publico do sistema e profissional autonomo,
# nao programador. Essas duas views trazem o mesmo ciclo pra dentro do
# painel: ver o estado, ler o QR Code e desconectar.


def _qrcode_como_imagem(dados):
    """Transforma o QR Code numa imagem que da pra por num <img>.

    A Evolution normalmente ja devolve o desenho pronto em base64. Mas
    nem sempre - as vezes vem so o conteudo em texto. Nesse caso eu
    mesmo desenho, com a mesma biblioteca que o comando do terminal usa.
    """
    pronto = dados.get("base64") or ""
    if pronto:
        # Ja vem como data URI em algumas versoes, e sem o prefixo em
        # outras. Normalizo os dois casos.
        if pronto.startswith("data:"):
            return pronto
        return f"data:image/png;base64,{pronto}"

    conteudo = dados.get("code") or ""
    if not conteudo:
        return None

    try:
        import base64
        import io

        import qrcode
    except ImportError:
        return None

    imagem = qrcode.make(conteudo)
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    codificado = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{codificado}"


@login_required
def conexao_whatsapp(request):
    """A tela onde o profissional conecta o WhatsApp lendo o QR Code.

    Cada prestador conecta o proprio numero. A sessao e a dele: quem
    entra aqui so mexe na sua, e as mensagens dos clientes dele saem
    desse numero.
    """
    profissional = obter_profissional(request)
    if profissional is None:
        # Conta de cliente ou usuario sem perfil de prestador. Nao tem
        # WhatsApp pra conectar porque nao tem agenda.
        return render(request, "painel/sem_perfil.html")

    # A instancia vem do profissional, nao do .env. E essa linha que faz
    # o QR Code desta tela ser o do numero dele.
    canal = CanalEvolution.do_profissional(profissional)

    if not canal.disponivel:
        return render(
            request,
            "notificacoes/conexao.html",
            {"configurado": False, "profissional": profissional},
        )

    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "desconectar":
            try:
                canal.desconectar()
                avisos.success(request, "WhatsApp desconectado.")
            except ErroEnvio as erro:
                avisos.error(request, str(erro))
            return redirect("conexao_whatsapp")

        if acao == "conectar":
            estado = canal.estado_da_conexao()
            if not estado["existe"]:
                try:
                    canal.criar_instancia()
                except ErroEnvio as erro:
                    # "ja existe" nao e problema: seguimos pro QR Code.
                    if "already in use" not in str(erro).lower():
                        avisos.error(request, str(erro))
                        return redirect("conexao_whatsapp")
            return redirect(f"{request.path}?ler=1")

    estado = canal.estado_da_conexao()
    qrcode_imagem = None
    codigo_pareamento = ""

    # So peco o QR Code quando a pessoa clicou em conectar. Pedir a toda
    # visita geraria um codigo novo sem necessidade e invalidaria o
    # anterior, que talvez ela ainda estivesse lendo.
    if request.GET.get("ler") and not estado["conectado"]:
        try:
            dados = canal.obter_qrcode()
            qrcode_imagem = _qrcode_como_imagem(dados)
            codigo_pareamento = dados.get("pairingCode") or ""
        except ErroEnvio as erro:
            avisos.error(request, str(erro))

    return render(
        request,
        "notificacoes/conexao.html",
        {
            "configurado": True,
            "profissional": profissional,
            "estado": estado,
            "qrcode_imagem": qrcode_imagem,
            "codigo_pareamento": codigo_pareamento,
            "url": canal.url,
            "instancia": canal.instancia,
        },
    )


@login_required
def estado_whatsapp(request):
    """Devolve o estado da conexao em JSON.

    A tela do QR Code chama isso de cinco em cinco segundos. Assim,
    quando a pessoa termina de ler o codigo no celular, a pagina se
    atualiza sozinha em vez de ela ficar apertando F5 sem saber se deu
    certo.
    """
    profissional = obter_profissional(request)
    if profissional is None:
        return JsonResponse({"conectado": False, "detalhe": "sem perfil"})

    canal = CanalEvolution.do_profissional(profissional)
    if not canal.disponivel:
        return JsonResponse({"conectado": False, "detalhe": "não configurado"})

    estado = canal.estado_da_conexao()
    return JsonResponse(
        {"conectado": estado["conectado"], "detalhe": estado["detalhe"]}
    )