"""
Enche o banco com dados de demonstracao.

Fiz esse comando pra nao ter que cadastrar tudo na mao toda vez que eu
preciso mostrar o sistema funcionando - na apresentacao, na reuniao com o
orientador ou depois de derrubar o banco pra testar a migracao de novo.

Tudo que ele cria usa e-mail terminado em @demo.local. Antes de montar,
ele apaga so essas contas, entao a minha conta de verdade e os dados que
eu cadastrei na mao continuam onde estao.

Rodar assim:

    python manage.py dados_demo

Escolhi tres profissionais de areas bem diferentes de proposito, pra
mostrar que o sistema nao e so pra area de estetica: um barbeiro, uma
tatuadora e um eletricista. A tatuadora usa o questionario de perguntas,
o eletricista nao usa - que e exatamente a diferenca que eu queria
demonstrar.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from agenda.models import (
    Agendamento,
    Bloqueio,
    DiaSemana,
    Disponibilidade,
    Pergunta,
    Resposta,
    Servico,
)
from clientes.models import Cliente, ConsentimentoOptIn, Feedback, PerfilCliente
from contas.models import AreaAtuacao, Local, Profissional
from notificacoes import envio as envio_notificacoes
from notificacoes.models import Notificacao

Usuario = get_user_model()

# Senha unica pra todas as contas de demonstracao. Em dado de verdade
# isso seria um absurdo, mas aqui e justamente pra eu nao ficar
# procurando senha no meio da apresentacao.
SENHA = "demo12345"

# O texto do aceite tem que ser o mesmo que a tela publica mostra, senao
# o consentimento gravado aqui nao representaria o que o cliente leu.
TEXTO_CONSENTIMENTO = (
    "Autorizo o envio de mensagens sobre este agendamento pelo WhatsApp. "
    "Posso cancelar o envio a qualquer momento respondendo SAIR."
)


class Command(BaseCommand):
    help = "Cria profissionais, servicos, clientes e agendamentos de exemplo."

    def handle(self, *args, **opcoes):
        # Tudo dentro de uma transacao: se quebrar no meio, nao fica
        # meio banco montado pra eu ter que limpar na mao.
        with transaction.atomic():
            self.limpar()
            areas = self.criar_areas()
            barbeiro = self.criar_barbeiro(areas["barbearia"])
            tatuadora = self.criar_tatuadora(areas["tatuagem"])
            eletricista = self.criar_eletricista(areas["eletrica"])
            self.criar_conta_de_cliente()
            self.criar_agendamentos(barbeiro, tatuadora, eletricista)
            self.criar_administrador()

        self.mostrar_acessos()

    # ------------------------------------------------------------------
    # Limpeza
    # ------------------------------------------------------------------

    def limpar(self):
        """Apaga o que este comando criou nas vezes anteriores.

        A ordem aqui nao e capricho. O Agendamento aponta pro Cliente e
        pro Servico com PROTECT, que e justamente pra ninguem apagar um
        servico e levar o historico junto sem perceber. Se eu tentasse
        apagar o usuario direto, o banco barrava por causa dessa
        protecao. Entao eu tiro primeiro os agendamentos, depois o que
        eles protegiam, e por ultimo a conta.

        Filtro so por @demo.local de proposito: a minha conta de verdade
        e o que eu cadastrei na mao continuam intactos.
        """
        usuarios = Usuario.objects.filter(email__endswith="@demo.local")
        profissionais = Profissional.objects.filter(usuario__in=usuarios)

        Agendamento.objects.filter(profissional__in=profissionais).delete()
        Cliente.objects.filter(profissional__in=profissionais).delete()
        Servico.objects.filter(profissional__in=profissionais).delete()

        apagados, _ = usuarios.delete()
        if apagados:
            self.stdout.write("Limpei a demonstração anterior.")

    # ------------------------------------------------------------------
    # Cadastros base
    # ------------------------------------------------------------------

    def criar_areas(self):
        """As areas de atuacao.

        Uso get_or_create porque area e cadastro compartilhado: se eu ja
        tinha criado "Barbearia" na mao, aproveito a que existe em vez
        de estourar erro de nome unico.
        """
        nomes = {
            "barbearia": "Barbearia",
            "tatuagem": "Tatuagem e piercing",
            "eletrica": "Serviços elétricos",
        }
        areas = {}
        for chave, nome in nomes.items():
            areas[chave], _ = AreaAtuacao.objects.get_or_create(
                slug=chave, defaults={"nome": nome}
            )
        return areas

    def criar_profissional(self, email, nome, slug, area, telefone, apresentacao):
        usuario = Usuario.objects.create_user(
            email=email, password=SENHA, first_name=nome.split()[0]
        )
        return Profissional.objects.create(
            usuario=usuario,
            nome_publico=nome,
            slug=slug,
            area=area,
            telefone=telefone,
            apresentacao=apresentacao,
        )

    def criar_barbeiro(self, area):
        """O caso mais simples: servico curto, sem questionario."""
        profissional = self.criar_profissional(
            email="marcos@demo.local",
            nome="Barbearia do Marcos",
            slug="marcos-barbeiro",
            area=area,
            telefone="+5577991110001",
            apresentacao=(
                "Corte, barba e acabamento. Atendo no salão e também "
                "a domicílio nas terças e quintas."
            ),
        )

        salao = Local.objects.create(
            profissional=profissional,
            nome="Barbearia Central",
            endereco="Rua Barão do Rio Branco, 145 — Centro, Guanambi",
            principal=True,
        )
        Local.objects.create(
            profissional=profissional,
            nome="Atendimento a domicílio",
            endereco="Na casa do cliente, combinado por mensagem",
        )

        self.criar_servicos(
            profissional,
            [
                ("Corte masculino", 30, 10, "35.00"),
                ("Barba", 20, 5, "25.00"),
                ("Corte + barba", 50, 10, "55.00"),
            ],
        )

        # Segunda a sexta em dois turnos, sabado so de manha. Os dois
        # turnos separados sao o que faz o horario do almoco nao aparecer
        # na grade do cliente.
        for dia in [DiaSemana.SEGUNDA, DiaSemana.TERCA, DiaSemana.QUARTA,
                    DiaSemana.QUINTA, DiaSemana.SEXTA]:
            self.criar_janela(profissional, salao, dia, "08:00", "12:00")
            self.criar_janela(profissional, salao, dia, "14:00", "18:00")
        self.criar_janela(profissional, salao, DiaSemana.SABADO, "08:00", "12:00")

        return profissional

    def criar_tatuadora(self, area):
        """A profissional que usa o questionario de perguntas.

        Aqui esta a parte que eu quero mostrar: o mesmo profissional tem
        um servico que pergunta (a tatuagem) e um que nao pergunta (o
        orcamento). O questionario e do servico, nao da pessoa.
        """
        profissional = self.criar_profissional(
            email="rafaela@demo.local",
            nome="Rafaela Nunes Tattoo",
            slug="rafaela-tattoo",
            area=area,
            telefone="+5577991110002",
            apresentacao=(
                "Estúdio de tatuagem com material descartável. "
                "Orçamento sem compromisso antes de fechar a sessão."
            ),
        )

        estudio = Local.objects.create(
            profissional=profissional,
            nome="Estúdio Agulha Fina",
            endereco="Av. Getúlio Vargas, 980 — Guanambi",
            principal=True,
        )

        tatuagem = self.criar_servicos(
            profissional,
            [
                ("Sessão de tatuagem pequena", 120, 30, "300.00"),
                ("Orçamento presencial", 20, 0, None),
            ],
        )[0]

        # Ligo o questionario so na tatuagem. O orcamento fica sem
        # nenhuma pergunta, igual estaria o servico de um eletricista.
        tatuagem.exige_perguntas = True
        tatuagem.save(update_fields=["exige_perguntas"])

        perguntas = [
            ("Você tem alergia a algum tipo de tinta ou metal?",
             Pergunta.Tipo.SIM_NAO, Pergunta.Alerta.SIM, True),
            ("Está usando algum medicamento contínuo?",
             Pergunta.Tipo.SIM_NAO, Pergunta.Alerta.SIM, True),
            ("Já fez tatuagem antes?",
             Pergunta.Tipo.SIM_NAO, Pergunta.Alerta.NENHUM, True),
            ("Descreva a ideia e a região do corpo.",
             Pergunta.Tipo.TEXTO, Pergunta.Alerta.NENHUM, True),
        ]
        for ordem, (texto, tipo, alerta, obrigatoria) in enumerate(perguntas):
            Pergunta.objects.create(
                servico=tatuagem,
                texto=texto,
                tipo=tipo,
                alerta_se=alerta,
                obrigatoria=obrigatoria,
                ordem=ordem,
            )

        for dia in [DiaSemana.TERCA, DiaSemana.QUINTA, DiaSemana.SABADO]:
            self.criar_janela(profissional, estudio, dia, "13:00", "19:00")

        # A tatuadora atende de hora em hora, nao de meia em meia: o
        # servico dela e longo, entao grade de 30 min so encheria a tela.
        profissional.granularidade_minutos = 60
        profissional.antecedencia_minima_horas = 24
        profissional.save(
            update_fields=["granularidade_minutos", "antecedencia_minima_horas"]
        )

        return profissional

    def criar_eletricista(self, area):
        """O profissional que nao tem nada a ver com estetica.

        Ele esta aqui pra provar o que a banca pediu: o sistema atende
        qualquer profissional autonomo. Nenhum servico dele faz
        pergunta, e ele nao atende em local fixo.
        """
        profissional = self.criar_profissional(
            email="joao@demo.local",
            nome="João Batista Elétrica",
            slug="joao-eletricista",
            area=area,
            telefone="+5577991110003",
            apresentacao=(
                "Instalação e manutenção elétrica residencial. "
                "Atendo em toda a cidade e região."
            ),
        )

        domicilio = Local.objects.create(
            profissional=profissional,
            nome="Atendimento a domicílio",
            endereco="No endereço do cliente",
            principal=True,
        )

        self.criar_servicos(
            profissional,
            [
                ("Visita técnica e orçamento", 60, 30, "120.00"),
                ("Instalação de chuveiro", 90, 30, "180.00"),
                ("Troca de disjuntor", 45, 15, "90.00"),
            ],
        )

        for dia in [DiaSemana.SEGUNDA, DiaSemana.TERCA, DiaSemana.QUARTA,
                    DiaSemana.QUINTA, DiaSemana.SEXTA]:
            self.criar_janela(profissional, domicilio, dia, "07:30", "17:00")

        return profissional

    def criar_servicos(self, profissional, linhas):
        criados = []
        for nome, duracao, intervalo, preco in linhas:
            criados.append(
                Servico.objects.create(
                    profissional=profissional,
                    nome=nome,
                    duracao_minutos=duracao,
                    intervalo_apos_minutos=intervalo,
                    preco=Decimal(preco) if preco else None,
                )
            )
        return criados

    def criar_janela(self, profissional, local, dia, inicio, fim):
        hora_inicio = timezone.datetime.strptime(inicio, "%H:%M").time()
        hora_fim = timezone.datetime.strptime(fim, "%H:%M").time()
        return Disponibilidade.objects.create(
            profissional=profissional,
            local=local,
            dia_semana=dia,
            hora_inicio=hora_inicio,
            hora_fim=hora_fim,
        )

    # ------------------------------------------------------------------
    # Conta de cliente
    # ------------------------------------------------------------------

    def criar_conta_de_cliente(self):
        """Uma conta de quem contrata servico.

        Serve pra mostrar o outro lado do cadastro: essa conta ve a
        vitrine com todos os prestadores e a lista dos proprios
        agendamentos, espalhados entre eles.

        O telefone e o mesmo que eu uso nos agendamentos la embaixo. E
        por ele que a tela "Meus agendamentos" acha os horarios dela em
        todos os profissionais.
        """
        usuario = Usuario.objects.create_user(
            email="ana@demo.local", password=SENHA, first_name="Ana"
        )
        return PerfilCliente.objects.create(
            usuario=usuario,
            nome="Ana Beatriz Rocha",
            telefone="+5577992220001",
        )

    def criar_administrador(self):
        Usuario.objects.create_superuser(
            email="admin@demo.local", password=SENHA, first_name="Admin"
        )

    # ------------------------------------------------------------------
    # Agenda
    # ------------------------------------------------------------------

    def criar_cliente(self, profissional, nome, telefone):
        """Cadastra o cliente na carteira do profissional, com aceite.

        Cliente sem consentimento nao recebe mensagem nenhuma, entao
        criar os dois juntos e o que deixa a fila de notificacoes com
        conteudo pra eu mostrar.
        """
        cliente = Cliente.objects.create(
            profissional=profissional, nome=nome, telefone=telefone
        )
        ConsentimentoOptIn.objects.create(
            cliente=cliente,
            origem=ConsentimentoOptIn.Origem.AGENDAMENTO_ONLINE,
            texto_apresentado=TEXTO_CONSENTIMENTO,
        )
        return cliente

    def dia_util(self, deslocamento):
        """Anda dias pra frente ou pra tras pulando domingo.

        Uso isso pra data nunca cair em dia que ninguem atende. E tudo
        calculado a partir de hoje, entao a demonstracao continua
        fazendo sentido daqui a tres meses - nao tem data escrita fixa
        no codigo.
        """
        passo = 1 if deslocamento >= 0 else -1
        restantes = abs(deslocamento)
        dia = timezone.localdate()
        while restantes:
            dia += timedelta(days=passo)
            if dia.isoweekday() != 7:
                restantes -= 1
        return dia

    def momento(self, deslocamento, hora, minuto=0):
        """Monta a data e hora com fuso, do jeito que o banco espera."""
        dia = self.dia_util(deslocamento)
        ingenuo = timezone.datetime.combine(
            dia, timezone.datetime.min.time().replace(hour=hora, minute=minuto)
        )
        return timezone.make_aware(ingenuo, timezone.get_current_timezone())

    def agendar(self, profissional, cliente, servico, inicio, status,
                observacoes="", motivo=""):
        local = profissional.locais.filter(principal=True).first()
        agendamento = Agendamento.objects.create(
            profissional=profissional,
            cliente=cliente,
            servico=servico,
            local=local,
            inicio=inicio,
            status=status,
            observacoes=observacoes,
            motivo_cancelamento=motivo,
        )

        # Preencho as datas que normalmente quem preenche e a view, pra
        # o historico da tela nao ficar com campo vazio.
        if status == Agendamento.Status.CONFIRMADO:
            agendamento.confirmado_em = agendamento.criado_em
            agendamento.save(update_fields=["confirmado_em"])
        if status == Agendamento.Status.CANCELADO:
            agendamento.cancelado_em = inicio - timedelta(hours=6)
            agendamento.save(update_fields=["cancelado_em"])

        return agendamento

    def criar_agendamentos(self, barbeiro, tatuadora, eletricista):
        """Monta o historico e a agenda futura dos tres.

        Ponho os cinco status de proposito: quero que a tela de
        agendamentos mostre o filtro funcionando, e que o painel tenha
        numero pra somar em vez de aparecer tudo zerado.
        """
        # -------------------------------------------------- barbeiro
        servicos = {s.nome: s for s in barbeiro.servicos.all()}
        ana = self.criar_cliente(barbeiro, "Ana Beatriz Rocha", "+5577992220001")
        carlos = self.criar_cliente(barbeiro, "Carlos Eduardo Lima", "+5577992220002")
        pedro = self.criar_cliente(barbeiro, "Pedro Henrique Alves", "+5577992220003")
        luana = self.criar_cliente(barbeiro, "Luana Martins", "+5577992220004")

        concluidos = [
            self.agendar(barbeiro, carlos, servicos["Corte masculino"],
                         self.momento(-7, 9), Agendamento.Status.CONCLUIDO),
            self.agendar(barbeiro, pedro, servicos["Corte + barba"],
                         self.momento(-5, 10), Agendamento.Status.CONCLUIDO),
            self.agendar(barbeiro, ana, servicos["Corte masculino"],
                         self.momento(-2, 15), Agendamento.Status.CONCLUIDO),
        ]
        faltou = self.agendar(barbeiro, luana, servicos["Barba"],
                              self.momento(-3, 14), Agendamento.Status.FALTOU)
        cancelado = self.agendar(
            barbeiro, carlos, servicos["Corte masculino"],
            self.momento(-1, 16), Agendamento.Status.CANCELADO,
            motivo="Cancelado pelo cliente",
        )

        futuros = [
            self.agendar(barbeiro, ana, servicos["Corte masculino"],
                         self.momento(1, 9), Agendamento.Status.CONFIRMADO),
            self.agendar(barbeiro, pedro, servicos["Barba"],
                         self.momento(1, 10), Agendamento.Status.SOLICITADO,
                         observacoes="Pediu para aparar bem curto."),
            self.agendar(barbeiro, luana, servicos["Corte + barba"],
                         self.momento(2, 14), Agendamento.Status.CONFIRMADO),
        ]

        # Avaliacao so de quem foi atendido. Deixei um concluido sem
        # nota de proposito, pra aparecer tambem o caso "ainda nao
        # avaliou".
        Feedback.objects.create(
            agendamento=concluidos[0], nota=5,
            comentario="Chegou no horário e o corte ficou exatamente como pedi.",
        )
        Feedback.objects.create(
            agendamento=concluidos[1], nota=4,
            comentario="Atendimento bom, só demorou um pouco para começar.",
        )

        # Folga marcada: serve pra eu mostrar que o bloqueio derruba o
        # dia inteiro da grade mesmo tendo disponibilidade cadastrada.
        Bloqueio.objects.create(
            profissional=barbeiro,
            inicio=self.momento(4, 0),
            fim=self.momento(4, 23, 59),
            motivo="Curso em Salvador",
        )

        # -------------------------------------------------- tatuadora
        tatuagem = tatuadora.servicos.get(nome="Sessão de tatuagem pequena")
        orcamento = tatuadora.servicos.get(nome="Orçamento presencial")
        ana_tattoo = self.criar_cliente(
            tatuadora, "Ana Beatriz Rocha", "+5577992220001"
        )
        bianca = self.criar_cliente(tatuadora, "Bianca Souza", "+5577992220005")

        # Esse e o agendamento que o questionario gera: ele fica
        # SOLICITADO esperando a profissional ler as respostas e decidir.
        pedido = self.agendar(
            tatuadora, bianca, tatuagem, self.momento(2, 15),
            Agendamento.Status.SOLICITADO,
            observacoes="Primeira tatuagem.",
        )
        respostas = {
            "Você tem alergia a algum tipo de tinta ou metal?": "sim",
            "Está usando algum medicamento contínuo?": "nao",
            "Já fez tatuagem antes?": "nao",
            "Descreva a ideia e a região do corpo.":
                "Uma flor pequena no antebraço esquerdo, linha fina.",
        }
        for pergunta in tatuagem.perguntas.all():
            Resposta.objects.create(
                agendamento=pedido,
                pergunta=pergunta,
                texto_pergunta=pergunta.texto,
                resposta=respostas[pergunta.texto],
                alerta_se=pergunta.alerta_se,
            )

        # Um orcamento normal, sem pergunta nenhuma, pro contraste ficar
        # visivel na mesma agenda.
        self.agendar(tatuadora, ana_tattoo, orcamento, self.momento(4, 13),
                     Agendamento.Status.CONFIRMADO)

        atendida = self.agendar(tatuadora, bianca, tatuagem, self.momento(-6, 15),
                                Agendamento.Status.CONCLUIDO)
        Feedback.objects.create(
            agendamento=atendida, nota=5,
            comentario="Muito cuidadosa e explicou todo o cuidado depois.",
        )
        concluidos.append(atendida)

        # -------------------------------------------------- eletricista
        visita = eletricista.servicos.get(nome="Visita técnica e orçamento")
        chuveiro = eletricista.servicos.get(nome="Instalação de chuveiro")
        ana_eletrica = self.criar_cliente(
            eletricista, "Ana Beatriz Rocha", "+5577992220001"
        )
        dona_maria = self.criar_cliente(
            eletricista, "Maria das Graças Pereira", "+5577992220006"
        )

        self.agendar(eletricista, ana_eletrica, chuveiro, self.momento(1, 8),
                     Agendamento.Status.CONFIRMADO,
                     observacoes="Apartamento 302, portão azul.")
        futuros.append(
            self.agendar(eletricista, dona_maria, visita, self.momento(3, 14),
                         Agendamento.Status.SOLICITADO)
        )
        concluidos.append(
            self.agendar(eletricista, dona_maria, visita, self.momento(-4, 9),
                         Agendamento.Status.CONCLUIDO)
        )

        self.ajustar_notificacoes(concluidos + [faltou], [cancelado])

    def ajustar_notificacoes(self, encerrados, cancelados):
        """Deixa o historico de mensagens coerente com o passado.

        O sinal cria toda notificacao como pendente, porque na vida real
        ela nasce antes de sair. Como aqui eu estou inventando um passado
        que nao aconteceu, eu marco como entregue o que teria saido - se
        nao fizesse isso, a tela de notificacoes mostraria dezenas de
        mensagens atrasadas e passaria a impressao de que o envio esta
        quebrado.
        """
        for agendamento in encerrados:
            # Atendimento concluido pede avaliacao. Na vida real quem
            # dispara e o sinal, quando o profissional muda a situacao;
            # aqui eu chamo na mao porque ja nasceu concluido.
            if agendamento.status == Agendamento.Status.CONCLUIDO:
                envio_notificacoes.pedir_avaliacao(agendamento)

            for notificacao in agendamento.notificacoes.all():
                notificacao.situacao = Notificacao.Situacao.ENTREGUE
                notificacao.canal = Notificacao.Canal.SIMULADO
                notificacao.enviada_em = notificacao.agendada_para
                notificacao.tentativas = 1
                notificacao.save(
                    update_fields=["situacao", "canal", "enviada_em", "tentativas"]
                )

        for agendamento in cancelados:
            agendamento.notificacoes.update(
                situacao=Notificacao.Situacao.CANCELADA
            )

    # ------------------------------------------------------------------
    # Saida na tela
    # ------------------------------------------------------------------

    def mostrar_acessos(self):
        """Imprime tudo que eu preciso saber pra entrar e testar."""
        linha = "-" * 62

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Dados de demonstração criados."))
        self.stdout.write(linha)
        self.stdout.write(f"Senha de todas as contas: {SENHA}")
        self.stdout.write(linha)

        self.stdout.write("PRESTADORES DE SERVIÇO (entram no painel)")
        for profissional in Profissional.objects.filter(
            usuario__email__endswith="@demo.local"
        ).select_related("usuario", "area"):
            self.stdout.write(
                f"  {profissional.usuario.email:<22} {profissional.nome_publico}"
            )
            self.stdout.write(
                f"  {'':<22} área: {profissional.area.nome}"
            )
            self.stdout.write(
                f"  {'':<22} página pública: /agendar/{profissional.slug}/"
            )

        self.stdout.write("")
        self.stdout.write("CLIENTE (vê a vitrine e os próprios agendamentos)")
        self.stdout.write("  ana@demo.local         Ana Beatriz Rocha")

        self.stdout.write("")
        self.stdout.write("ADMINISTRADOR (/admin/)")
        self.stdout.write("  admin@demo.local")
        self.stdout.write(linha)

        self.stdout.write(
            f"Serviços: {Servico.objects.count()}  |  "
            f"Agendamentos: {Agendamento.objects.count()}  |  "
            f"Perguntas: {Pergunta.objects.count()}  |  "
            f"Notificações: {Notificacao.objects.count()}"
        )
        self.stdout.write(linha)
        self.stdout.write(
            "Para ver o questionário funcionando: entre com rafaela@demo.local,\n"
            "abra Agendamentos e clique no pedido da Bianca Souza. A resposta\n"
            "da alergia aparece destacada em vermelho."
        )
        self.stdout.write("")