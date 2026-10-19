from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.text import slugify

from agenda.horarios import taxa_de_falta
from agenda.models import Agendamento
from clientes.models import Cliente, PerfilCliente

from .models import AreaAtuacao, Local, Profissional, normalizar_telefone

Usuario = get_user_model()


def home(request):
    """A pagina inicial publica: a vitrine do sistema.

    Antes o "/" mandava direto pro painel, e quem abrisse o endereco
    sem estar logado caia na tela de login sem entender o que era o
    sistema. Agora essa tela explica a proposta, mostra os numeros do
    questionario e so depois oferece o login.
    """
    return render(request, "publico/home.html")


def _slug_livre(nome, modelo=Profissional, padrao="profissional"):
    """Monta um identificador que ainda nao esteja em uso.

    O slug e o endereco publico do profissional, entao ele e unico no
    sistema inteiro. Se ja existe um "joao", o proximo vira "joao-2".
    Melhor isso do que recusar o cadastro e mandar a pessoa inventar
    outro nome.

    Serve tambem pra area de atuacao, que tem a mesma necessidade. O
    limite eu leio do proprio campo em vez de escrever o numero na mao:
    o slug do profissional aceita 120 caracteres e o da area, 80.

    O "padrao" entra quando o slugify devolve vazio - acontece com nome
    escrito so com simbolo. Sem ele, o primeiro que fizesse isso gravava
    slug em branco e o segundo batia na restricao de unico.
    """
    limite = modelo._meta.get_field("slug").max_length
    base = slugify(nome)[: limite - 4] or padrao
    candidato = base
    numero = 2
    while modelo.objects.filter(slug=candidato).exists():
        candidato = f"{base}-{numero}"
        numero += 1
    return candidato


def _area_de_atuacao(texto):
    """Acha a area pelo que a pessoa digitou, ou cria uma nova.

    Procuro pelo NOME antes do slug. Os dois sao unicos, e nem sempre
    andam juntos: tem area gravada como "Servicos eletricos" com slug
    "eletrica". Olhando so o slug eu nao achava essa area, tentava criar
    outra com o mesmo nome e o banco recusava - erro 500 no cadastro de
    quem digitasse o nome certo de uma area que ja existe.
    """
    nome = texto[:80]

    # iexact pra "barbearia" achar "Barbearia". Quem digita no celular
    # raramente acerta a maiuscula.
    area = AreaAtuacao.objects.filter(nome__iexact=nome).first()
    if area is None:
        area = AreaAtuacao.objects.filter(slug=slugify(nome)[:80]).first()
    if area is None:
        area = AreaAtuacao.objects.create(
            nome=nome, slug=_slug_livre(nome, AreaAtuacao, "area")
        )
    return area


def _dados_do_formulario(request):
    """Tira do POST os campos do perfil, ja sem espaco sobrando."""
    return {
        "nome": request.POST.get("nome", "").strip(),
        "email": request.POST.get("email", "").strip().lower(),
        "telefone": request.POST.get("telefone", "").strip(),
        "area": request.POST.get("area", "").strip(),
        "local_nome": request.POST.get("local_nome", "").strip(),
        "local_endereco": request.POST.get("local_endereco", "").strip(),
    }


def cadastro(request):
    """Cadastro do profissional, pelos dois caminhos.

    Ate agora so dava pra criar profissional pelo admin do Django, o que
    na pratica queria dizer que so eu conseguia.

    Essa view atende as duas entradas:

    - quem chega deslogado preenche tudo, inclusive e-mail e senha;
    - quem entrou com o Google ja tem conta, mas nao tem perfil de
      profissional - o Google so devolve nome e e-mail. Entao o
      formulario aparece sem a parte de senha, so pedindo o que falta.

    Juntei as duas numa view so porque a regra do negocio e a mesma. Se
    eu tivesse feito duas, toda mudanca de campo teria que ser feita em
    dois lugares.
    """
    # Quem ja tem perfil completo, dos dois tipos, nao tem o que fazer
    # aqui.
    if request.user.is_authenticated:
        if obter_profissional(request) is not None:
            return redirect("painel")
        if getattr(request.user, "perfil_cliente", None) is not None:
            return redirect("explorar")

    # Veio do Google: ja esta logado, so falta escolher o tipo e
    # preencher o que o Google nao informa.
    completando = request.user.is_authenticated

    areas = AreaAtuacao.objects.filter(ativo=True).order_by("nome")

    # Aproveito o nome que o Google mandou pra ja deixar preenchido.
    dados = {"nome": request.user.get_full_name() or ""} if completando else {}
    tipo = request.POST.get("tipo", "prestador")

    if request.method == "POST":
        dados = _dados_do_formulario(request)
        dados["tipo"] = tipo
        senha = request.POST.get("senha", "")
        senha2 = request.POST.get("senha2", "")

        erros = []

        if len(dados["nome"]) < 3:
            erros.append("Informe seu nome completo.")

        telefone = normalizar_telefone(dados["telefone"])
        if not telefone:
            erros.append("Informe um telefone válido com DDD, como (77) 99999-8888.")
        elif tipo == "cliente":
            # O telefone da conta de cliente e unico: e por ele que o
            # sistema acha os agendamentos da pessoa em todos os
            # prestadores. Confiro aqui pra dar uma mensagem decente em
            # vez de deixar o banco estourar erro 500 na cara dela.
            repetidos = PerfilCliente.objects.filter(telefone=telefone)
            if completando:
                repetidos = repetidos.exclude(usuario=request.user)
            if repetidos.exists():
                erros.append("Já existe uma conta de cliente com esse telefone.")

        # Area so e obrigatoria pra quem presta servico. Cliente nao
        # tem area de atuacao nenhuma.
        if tipo == "prestador" and not dados["area"]:
            erros.append("Informe sua área de atuação.")

        # E-mail e senha so entram na conta quando e cadastro do zero.
        if not completando:
            if not dados["email"]:
                erros.append("Informe um e-mail.")
            elif Usuario.objects.filter(email=dados["email"]).exists():
                erros.append("Já existe uma conta com esse e-mail.")

            if len(senha) < 8:
                erros.append("A senha precisa ter pelo menos 8 caracteres.")
            elif senha != senha2:
                erros.append("As duas senhas não são iguais.")

        if erros:
            for erro in erros:
                messages.error(request, erro)
            return render(
                request,
                "registration/cadastro.html",
                {"areas": areas, "dados": dados, "completando": completando,
                 "tipo": tipo},
            )

        # Tudo ou nada: conta sem perfil seria uma conta que nao serve
        # pra nada.
        with transaction.atomic():
            if completando:
                usuario = request.user
            else:
                usuario = Usuario.objects.create_user(
                    email=dados["email"],
                    password=senha,
                    first_name=dados["nome"].split()[0][:150],
                )

            if tipo == "cliente":
                PerfilCliente.objects.create(
                    usuario=usuario, nome=dados["nome"], telefone=telefone
                )
                destino = "explorar"
                aviso = "Conta criada. Agora é só escolher com quem agendar."
            else:
                # A area pode ser uma da lista ou uma que a pessoa
                # digitou na hora. E isso que faz o sistema atender
                # qualquer profissao sem eu mexer no codigo.
                area = _area_de_atuacao(dados["area"])
                profissional = Profissional.objects.create(
                    usuario=usuario,
                    nome_publico=dados["nome"],
                    slug=_slug_livre(dados["nome"]),
                    area=area,
                    telefone=telefone,
                )
                if dados["local_nome"]:
                    Local.objects.create(
                        profissional=profissional,
                        nome=dados["local_nome"],
                        endereco=dados["local_endereco"],
                        principal=True,
                    )
                destino = "servicos"
                aviso = (
                    f"Cadastro concluído. Seu link de agendamento é "
                    f"/agendar/{profissional.slug}/"
                )

        if not completando:
            # Entra direto, sem mandar a pessoa digitar a senha de novo.
            #
            # O backend vai explicito porque agora existem dois: o do
            # Django, que cuida do e-mail e senha, e o do allauth, que
            # cuida do Google. Sem dizer qual, o Django nao adivinha e
            # levanta erro.
            login(
                request,
                usuario,
                backend="django.contrib.auth.backends.ModelBackend",
            )

        messages.success(request, aviso)
        return redirect(destino)

    return render(
        request,
        "registration/cadastro.html",
        {"areas": areas, "dados": dados, "completando": completando,
         "tipo": tipo},
    )


def obter_profissional(request):
    """Devolve o profissional do usuário logado, ou None."""
    return getattr(request.user, "profissional", None)


@login_required
def painel(request):
    """Visão do dia: o que acontece hoje e os indicadores do mês."""
    profissional = obter_profissional(request)
    if profissional is None:
        # Quem tem conta de cliente nao tem painel de profissional: o
        # lugar dele e a vitrine.
        if getattr(request.user, "perfil_cliente", None) is not None:
            return redirect("explorar")
        return render(request, "painel/sem_perfil.html")

    agora = timezone.localtime()
    hoje = agora.date()
    inicio_hoje = timezone.make_aware(
        timezone.datetime.combine(hoje, timezone.datetime.min.time()),
        timezone.get_current_timezone(),
    )
    fim_hoje = inicio_hoje + timedelta(days=1)

    agendamentos_hoje = (
        Agendamento.objects.filter(
            profissional=profissional,
            inicio__gte=inicio_hoje,
            inicio__lt=fim_hoje,
        )
        .select_related("cliente", "servico", "local")
        .order_by("inicio")
    )

    proximos = (
        Agendamento.objects.filter(
            profissional=profissional,
            inicio__gte=fim_hoje,
            status__in=Agendamento.STATUS_OCUPAM_AGENDA,
        )
        .select_related("cliente", "servico")
        .order_by("inicio")[:8]
    )

    inicio_mes = inicio_hoje.replace(day=1)
    indicadores = taxa_de_falta(profissional, inicio=inicio_mes)

    contexto = {
        "profissional": profissional,
        "hoje": hoje,
        "agendamentos_hoje": agendamentos_hoje,
        "proximos": proximos,
        "total_hoje": agendamentos_hoje.count(),
        "confirmados_hoje": agendamentos_hoje.filter(
            status=Agendamento.Status.CONFIRMADO
        ).count(),
        "aguardando": Agendamento.objects.filter(
            profissional=profissional,
            status=Agendamento.Status.SOLICITADO,
            inicio__gte=agora,
        ).count(),
        "total_clientes": Cliente.objects.filter(profissional=profissional).count(),
        "indicadores": indicadores,
        "link_publico": request.build_absolute_uri(f"/agendar/{profissional.slug}/"),
    }
    return render(request, "painel/painel.html", contexto)