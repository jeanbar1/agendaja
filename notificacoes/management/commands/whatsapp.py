"""
Administra a conexão do WhatsApp.

Concentra em um comando o ciclo de vida da sessão: verificar estado, criar
a instância, exibir o QR Code para vincular o número e disparar uma
mensagem de teste. Sem isso seria preciso montar as requisições à mão a
cada vez que a sessão caísse.

Uso:
    python manage.py whatsapp                        estado atual
    python manage.py whatsapp --conectar             cria e mostra o QR Code
    python manage.py whatsapp --testar +5577999998888
    python manage.py whatsapp --desconectar
"""

import io

from django.core.management.base import BaseCommand, CommandError

from notificacoes.canais import CanalEvolution, ErroEnvio, obter_canal


class Command(BaseCommand):
    help = "Verifica e configura a conexão do WhatsApp."

    def add_arguments(self, parser):
        parser.add_argument(
            "--conectar",
            action="store_true",
            help="Cria a instância, se necessário, e exibe o QR Code.",
        )
        parser.add_argument(
            "--testar",
            metavar="TELEFONE",
            help="Envia uma mensagem de teste para o número informado.",
        )
        parser.add_argument(
            "--desconectar",
            action="store_true",
            help="Encerra a sessão do WhatsApp.",
        )

    # -- Auxiliares ------------------------------------------------------

    def _titulo(self, texto):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(texto))

    def _ok(self, texto):
        self.stdout.write(self.style.SUCCESS(f"  {texto}"))

    def _aviso(self, texto):
        self.stdout.write(self.style.WARNING(f"  {texto}"))

    def _erro(self, texto):
        self.stdout.write(self.style.ERROR(f"  {texto}"))

    def _canal_evolution(self):
        canal = CanalEvolution()
        if not canal.disponivel:
            raise CommandError(
                "Evolution não configurada. Preencha no .env:\n"
                "  EVOLUTION_URL=http://localhost:8080\n"
                "  EVOLUTION_INSTANCIA=agendaja\n"
                "  EVOLUTION_API_KEY=<a mesma chave do docker-compose.yml>"
            )
        return canal

    # -- Execução --------------------------------------------------------

    def handle(self, *args, **opcoes):
        ativo = obter_canal()
        self._titulo("Canal configurado")
        self.stdout.write(f"  WHATSAPP_CANAL define: {ativo.nome}")

        if ativo.nome == "simulado":
            self._aviso(
                "Operando em simulação: as mensagens são registradas, "
                "mas não saem."
            )

        canal = self._canal_evolution()
        self.stdout.write(f"  Evolution em {canal.url}, instância '{canal.instancia}'")

        if opcoes["desconectar"]:
            return self._desconectar(canal)
        if opcoes["conectar"]:
            return self._conectar(canal)
        if opcoes["testar"]:
            return self._testar(canal, opcoes["testar"])

        self._mostrar_estado(canal)

    def _mostrar_estado(self, canal):
        self._titulo("Estado da conexão")
        estado = canal.estado_da_conexao()

        if not estado["existe"]:
            self._erro(estado["detalhe"])
            self.stdout.write("")
            self.stdout.write("  Se o contêiner não está no ar:")
            self.stdout.write("      docker compose up -d")
            self.stdout.write("  Se a instância ainda não existe:")
            self.stdout.write("      python manage.py whatsapp --conectar")
            return

        if estado["conectado"]:
            self._ok("Conectado. As mensagens já saem pelo WhatsApp.")
        else:
            self._aviso(
                f"Instância existe, mas o estado é '{estado['detalhe']}'. "
                "Rode --conectar para ler o QR Code."
            )

    def _conectar(self, canal):
        self._titulo("Preparando a conexão")

        estado = canal.estado_da_conexao()
        if estado["conectado"]:
            self._ok("Já está conectado. Nada a fazer.")
            return

        if not estado["existe"]:
            self.stdout.write("  Criando a instância...")
            try:
                canal.criar_instancia()
                self._ok("Instância criada.")
            except ErroEnvio as erro:
                # "já existe" não é problema: seguimos para o QR Code.
                if "already in use" in str(erro).lower():
                    self._aviso("A instância já existia.")
                else:
                    raise CommandError(str(erro))

        self.stdout.write("  Pedindo o QR Code...")
        try:
            dados = canal.obter_qrcode()
        except ErroEnvio as erro:
            raise CommandError(str(erro))

        conteudo = dados.get("code") or ""
        pareamento = dados.get("pairingCode") or ""

        if not conteudo:
            self._aviso(
                "A Evolution não devolveu QR Code. Talvez já esteja "
                "conectada — rode o comando sem argumentos para conferir."
            )
            return

        self._titulo("Leia o QR Code com o WhatsApp")
        self.stdout.write(
            "  No celular: WhatsApp > Configurações > Aparelhos conectados"
            " > Conectar aparelho\n"
        )
        self._desenhar_qrcode(conteudo)

        if pareamento:
            self.stdout.write("")
            self.stdout.write(
                f"  Alternativa sem câmera — código de pareamento: "
                f"{self.style.SUCCESS(pareamento)}"
            )

        self.stdout.write("")
        self.stdout.write("  Depois de ler, confirme com:")
        self.stdout.write("      python manage.py whatsapp")

    def _desenhar_qrcode(self, conteudo):
        """Desenha o QR Code no próprio terminal.

        Evita o vaivém de salvar imagem e abrir em outro programa — o
        celular lê direto da tela.
        """
        try:
            import qrcode
        except ImportError:
            self._aviso(
                "Instale a biblioteca para ver o QR Code aqui: "
                "pip install qrcode"
            )
            self.stdout.write(f"\n  Conteúdo do QR:\n  {conteudo}\n")
            return

        qr = qrcode.QRCode(border=2)
        qr.add_data(conteudo)
        qr.make(fit=True)

        # O OutputWrapper do Django acrescenta quebra de linha a cada
        # escrita, o que desmontaria o desenho. Monta-se tudo em memória
        # e escreve de uma vez só, sem terminador extra.
        buffer = io.StringIO()
        qr.print_ascii(out=buffer, invert=True)
        self.stdout.write(buffer.getvalue(), ending="")

    def _testar(self, canal, telefone):
        self._titulo("Envio de teste")

        if not telefone.startswith("+"):
            telefone = f"+{telefone.lstrip('+')}"

        estado = canal.estado_da_conexao()
        if not estado["conectado"]:
            raise CommandError(
                f"Não conectado (estado: {estado['detalhe']}). "
                "Rode: python manage.py whatsapp --conectar"
            )

        texto = (
            "Teste do AgendaJá.\n\n"
            "Se você recebeu esta mensagem, a integração com o WhatsApp "
            "está funcionando."
        )

        try:
            identificador = canal.enviar_texto(telefone, texto)
        except ErroEnvio as erro:
            raise CommandError(str(erro))

        self._ok(f"Enviada para {telefone}.")
        if identificador:
            self.stdout.write(f"  Identificador: {identificador}")

    def _desconectar(self, canal):
        self._titulo("Encerrando a sessão")
        canal.desconectar()
        self._ok("Sessão encerrada. Para reconectar, rode --conectar.")
