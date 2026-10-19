"""
Testes do motor de horarios e das regras da agenda.

Esse e o pedaco do sistema onde um erro passa despercebido: se o motor
oferecer um horario que na verdade esta ocupado, ninguem descobre ate o
cliente chegar e dar de cara com outra pessoa sendo atendida. Por isso
testei aqui e nao nas telas.

Uma coisa que aprendi na marra: teste que depende do relogio de verdade
quebra sozinho. Escrevi a primeira versao em agosto usando datas fixas,
e em setembro metade falhou porque as datas tinham virado passado. Agora
todo teste fixa o proprio relogio no ``AGORA`` e passa ele pra dentro
das funcoes - assim o resultado e o mesmo hoje, amanha e no dia da
defesa.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from agenda.horarios import (
    horario_esta_livre,
    horarios_disponiveis,
    primeiro_horario_livre,
    proximos_dias_com_horarios,
    taxa_de_falta,
)
from agenda.models import Agendamento, Bloqueio, Disponibilidade, Servico
from clientes.models import Cliente
from contas.models import AreaAtuacao, Local, Profissional

Usuario = get_user_model()

# Relogio fixo de todos os testes. Escolhi uma data qualquer no futuro;
# o que importa e que ela nunca muda.
DIA = date(2026, 3, 10)
HORA_DE_AGORA = time(6, 0)


class BaseAgenda(TestCase):
    """Monta um profissional completo antes de cada teste."""

    def setUp(self):
        self.agora = timezone.make_aware(
            datetime.combine(DIA, HORA_DE_AGORA), timezone.get_current_timezone()
        )

        self.usuario = Usuario.objects.create_user(
            email="teste@exemplo.com", password="senha-de-teste"
        )
        self.area = AreaAtuacao.objects.create(nome="Barbearia", slug="barbearia")
        self.profissional = Profissional.objects.create(
            usuario=self.usuario,
            nome_publico="Profissional de Teste",
            slug="profissional-de-teste",
            area=self.area,
            telefone="+5577999990000",
            granularidade_minutos=30,
            antecedencia_minima_horas=1,
            antecedencia_maxima_dias=60,
        )
        self.local = Local.objects.create(
            profissional=self.profissional, nome="Sala 1", principal=True
        )

        # Servico de 30 minutos, sem intervalo: o mais simples de contar
        # na mao quando um teste falha.
        self.servico = Servico.objects.create(
            profissional=self.profissional,
            nome="Corte",
            duracao_minutos=30,
            intervalo_apos_minutos=0,
            preco=Decimal("35.00"),
        )

        # Janela das 08:00 as 12:00 no mesmo dia da semana do DIA. Uso
        # isoweekday() em vez de escrever o numero na mao pra nao
        # precisar conferir em que dia da semana o DIA caiu.
        self.janela = Disponibilidade.objects.create(
            profissional=self.profissional,
            local=self.local,
            dia_semana=DIA.isoweekday(),
            hora_inicio=time(8, 0),
            hora_fim=time(12, 0),
        )

        self.cliente = Cliente.objects.create(
            profissional=self.profissional,
            nome="Cliente de Teste",
            telefone="+5577999991111",
        )

    # -- Auxiliares ------------------------------------------------------

    def momento(self, hora, minuto=0, dia=None):
        """Atalho pra montar um datetime com fuso no dia de teste."""
        return timezone.make_aware(
            datetime.combine(dia or DIA, time(hora, minuto)),
            timezone.get_current_timezone(),
        )

    def grade(self, servico=None, dia=None, local=None):
        """Roda o motor com o relogio travado."""
        return horarios_disponiveis(
            self.profissional,
            servico or self.servico,
            dia or DIA,
            local=local,
            agora=self.agora,
        )

    def como_texto(self, horarios):
        return [h.strftime("%H:%M") for h in horarios]

    def agendar(self, hora, minuto=0, status=Agendamento.Status.CONFIRMADO,
                servico=None, dia=None):
        return Agendamento.objects.create(
            profissional=self.profissional,
            cliente=self.cliente,
            servico=servico or self.servico,
            local=self.local,
            inicio=self.momento(hora, minuto, dia),
            status=status,
        )


class MontagemDaGrade(BaseAgenda):
    """A grade que o cliente ve, sem nada atrapalhando."""

    def test_janela_de_quatro_horas_rende_oito_horarios(self):
        # 08:00 as 12:00, de 30 em 30, servico de 30 min.
        horarios = self.grade()
        self.assertEqual(len(horarios), 8)
        self.assertEqual(self.como_texto(horarios)[0], "08:00")
        self.assertEqual(self.como_texto(horarios)[-1], "11:30")

    def test_servico_longo_nao_ultrapassa_o_fim_da_janela(self):
        # Servico de 2h numa janela de 4h: so cabe ate as 10:00.
        longo = Servico.objects.create(
            profissional=self.profissional, nome="Longo",
            duracao_minutos=120, intervalo_apos_minutos=0,
        )
        self.assertEqual(self.como_texto(self.grade(servico=longo)),
                         ["08:00", "08:30", "09:00", "09:30", "10:00"])

    def test_intervalo_apos_atendimento_conta_na_duracao(self):
        # 40 min de atendimento + 20 de intervalo = 1h ocupada. O ultimo
        # que cabe comeca as 11:00.
        com_intervalo = Servico.objects.create(
            profissional=self.profissional, nome="Com intervalo",
            duracao_minutos=40, intervalo_apos_minutos=20,
        )
        horarios = self.como_texto(self.grade(servico=com_intervalo))
        self.assertEqual(horarios[-1], "11:00")

    def test_duas_janelas_no_mesmo_dia(self):
        # Almoco: 08-12 e 14-18. Nao pode sobrar horario entre 12 e 14.
        Disponibilidade.objects.create(
            profissional=self.profissional, local=self.local,
            dia_semana=DIA.isoweekday(),
            hora_inicio=time(14, 0), hora_fim=time(18, 0),
        )
        horarios = self.como_texto(self.grade())
        self.assertEqual(len(horarios), 16)
        self.assertNotIn("12:00", horarios)
        self.assertNotIn("13:00", horarios)
        self.assertIn("14:00", horarios)

    def test_granularidade_de_quinze_minutos(self):
        self.profissional.granularidade_minutos = 15
        self.profissional.save()
        horarios = self.como_texto(self.grade())
        self.assertEqual(len(horarios), 15)
        self.assertEqual(horarios[1], "08:15")

    def test_dia_sem_janela_nao_oferece_nada(self):
        # O dia seguinte nao tem disponibilidade cadastrada.
        self.assertEqual(self.grade(dia=DIA + timedelta(days=1)), [])

    def test_profissional_inativo_nao_oferece_nada(self):
        self.profissional.ativo = False
        self.profissional.save()
        self.assertEqual(self.grade(), [])

    def test_servico_inativo_nao_oferece_nada(self):
        self.servico.ativo = False
        self.servico.save()
        self.assertEqual(self.grade(), [])

    def test_filtro_por_local(self):
        outro = Local.objects.create(profissional=self.profissional, nome="Sala 2")
        self.assertEqual(len(self.grade(local=self.local)), 8)
        self.assertEqual(self.grade(local=outro), [])


class Antecedencia(BaseAgenda):
    """As regras de "cedo demais" e "longe demais"."""

    def test_antecedencia_minima_corta_os_horarios_proximos(self):
        # Sao 06:00 e a antecedencia minima e de 3h: some tudo antes
        # das 09:00.
        self.profissional.antecedencia_minima_horas = 3
        self.profissional.save()
        horarios = self.como_texto(self.grade())
        self.assertEqual(horarios[0], "09:00")
        self.assertNotIn("08:00", horarios)

    def test_antecedencia_minima_pode_zerar_o_dia(self):
        self.profissional.antecedencia_minima_horas = 24
        self.profissional.save()
        self.assertEqual(self.grade(), [])

    def test_antecedencia_maxima_corta_dias_distantes(self):
        # So aceita 7 dias pra frente; o dia +14 fica fora.
        self.profissional.antecedencia_maxima_dias = 7
        self.profissional.save()
        Disponibilidade.objects.create(
            profissional=self.profissional, local=self.local,
            dia_semana=(DIA + timedelta(days=14)).isoweekday(),
            hora_inicio=time(8, 0), hora_fim=time(12, 0),
        )
        self.assertEqual(self.grade(dia=DIA + timedelta(days=14)), [])


class ConflitoComAgendamento(BaseAgenda):
    """Horario ocupado nao pode ser oferecido de novo."""

    def test_agendamento_tira_o_proprio_horario(self):
        self.agendar(9)
        self.assertNotIn("09:00", self.como_texto(self.grade()))

    def test_agendamento_tira_tambem_o_horario_anterior_que_invadiria(self):
        # Servico de 1h, agendado as 09:00. O de 08:30 terminaria as
        # 09:30 e invadiria - entao tem que sumir tambem.
        uma_hora = Servico.objects.create(
            profissional=self.profissional, nome="Uma hora",
            duracao_minutos=60, intervalo_apos_minutos=0,
        )
        self.agendar(9, servico=uma_hora)
        horarios = self.como_texto(self.grade(servico=uma_hora))
        self.assertNotIn("08:30", horarios)
        self.assertNotIn("09:00", horarios)
        self.assertIn("08:00", horarios)
        self.assertIn("10:00", horarios)

    def test_status_solicitado_ja_ocupa(self):
        self.agendar(9, status=Agendamento.Status.SOLICITADO)
        self.assertNotIn("09:00", self.como_texto(self.grade()))

    def test_cancelado_devolve_o_horario(self):
        agendamento = self.agendar(9)
        self.assertNotIn("09:00", self.como_texto(self.grade()))

        agendamento.status = Agendamento.Status.CANCELADO
        agendamento.save()
        self.assertIn("09:00", self.como_texto(self.grade()))

    def test_concluido_e_falta_devolvem_o_horario(self):
        agendamento = self.agendar(9)
        for status in (Agendamento.Status.CONCLUIDO, Agendamento.Status.FALTOU):
            agendamento.status = status
            agendamento.save()
            self.assertIn("09:00", self.como_texto(self.grade()),
                          f"status {status} deveria liberar o horario")

    def test_agendamento_de_outro_profissional_nao_atrapalha(self):
        outro_usuario = Usuario.objects.create_user(
            email="outro@exemplo.com", password="x"
        )
        outro = Profissional.objects.create(
            usuario=outro_usuario, nome_publico="Outro", slug="outro",
            area=self.area, telefone="+5577999992222",
        )
        outro_servico = Servico.objects.create(
            profissional=outro, nome="Corte", duracao_minutos=30
        )
        outro_cliente = Cliente.objects.create(
            profissional=outro, nome="Cliente do outro", telefone="+5577999993333"
        )
        Agendamento.objects.create(
            profissional=outro, cliente=outro_cliente, servico=outro_servico,
            inicio=self.momento(9), status=Agendamento.Status.CONFIRMADO,
        )
        self.assertIn("09:00", self.como_texto(self.grade()))


class ConflitoComBloqueio(BaseAgenda):
    """Folga e ferias tem prioridade sobre a grade da semana."""

    def test_bloqueio_de_dia_inteiro_zera_a_grade(self):
        Bloqueio.objects.create(
            profissional=self.profissional,
            inicio=self.momento(0), fim=self.momento(23, 59),
            motivo="Folga",
        )
        self.assertEqual(self.grade(), [])

    def test_bloqueio_parcial_remove_so_a_faixa(self):
        Bloqueio.objects.create(
            profissional=self.profissional,
            inicio=self.momento(8), fim=self.momento(10),
            motivo="Consulta",
        )
        horarios = self.como_texto(self.grade())
        self.assertEqual(horarios, ["10:00", "10:30", "11:00", "11:30"])

    def test_bloqueio_de_varios_dias(self):
        # Ferias cobrindo o DIA inteiro, comecando na vespera.
        Bloqueio.objects.create(
            profissional=self.profissional,
            inicio=self.momento(0, dia=DIA - timedelta(days=1)),
            fim=self.momento(0, dia=DIA + timedelta(days=3)),
            motivo="Férias",
        )
        self.assertEqual(self.grade(), [])

    def test_remover_bloqueio_devolve_os_horarios(self):
        bloqueio = Bloqueio.objects.create(
            profissional=self.profissional,
            inicio=self.momento(8), fim=self.momento(10),
        )
        self.assertEqual(len(self.grade()), 4)
        bloqueio.delete()
        self.assertEqual(len(self.grade()), 8)


class VerificacaoDeHorarioPontual(BaseAgenda):
    """A funcao que a view chama antes de gravar."""

    def test_horario_vazio_esta_livre(self):
        self.assertTrue(
            horario_esta_livre(self.profissional, self.momento(9), self.momento(9, 30))
        )

    def test_horario_ocupado_nao_esta_livre(self):
        self.agendar(9)
        self.assertFalse(
            horario_esta_livre(self.profissional, self.momento(9), self.momento(9, 30))
        )

    def test_encostar_no_fim_do_outro_nao_e_conflito(self):
        # A comparacao e semiaberta: quem termina as 09:30 nao conflita
        # com quem comeca as 09:30.
        self.agendar(9)
        self.assertTrue(
            horario_esta_livre(
                self.profissional, self.momento(9, 30), self.momento(10)
            )
        )

    def test_ignorar_o_proprio_agendamento_permite_remarcar(self):
        agendamento = self.agendar(9)
        self.assertTrue(
            horario_esta_livre(
                self.profissional, self.momento(9), self.momento(9, 30),
                ignorar_agendamento=agendamento.pk,
            )
        )


class ConsultasDeApoio(BaseAgenda):
    """As funcoes que alimentam a regua de dias da tela publica."""

    def test_so_traz_dias_que_tem_vaga(self):
        dias = proximos_dias_com_horarios(
            self.profissional, self.servico, a_partir_de=DIA, dias=8,
            agora=self.agora,
        )
        # So cadastrei um dia da semana, entao em 8 dias aparecem dois.
        self.assertEqual(len(dias), 2)
        self.assertEqual(dias[0][0], DIA)

    def test_primeiro_horario_livre(self):
        primeiro = primeiro_horario_livre(
            self.profissional, self.servico, a_partir_de=DIA, agora=self.agora
        )
        self.assertEqual(primeiro.strftime("%H:%M"), "08:00")

    def test_primeiro_horario_pula_o_que_esta_ocupado(self):
        self.agendar(8)
        primeiro = primeiro_horario_livre(
            self.profissional, self.servico, a_partir_de=DIA, agora=self.agora
        )
        self.assertEqual(primeiro.strftime("%H:%M"), "08:30")

    def test_sem_disponibilidade_devolve_none(self):
        self.janela.delete()
        self.assertIsNone(
            primeiro_horario_livre(
                self.profissional, self.servico, a_partir_de=DIA, agora=self.agora
            )
        )


class RestricaoDoBanco(BaseAgenda):
    """A trava de sobreposicao do PostgreSQL.

    E a parte que eu mais defendo do projeto. O motor de horarios evita
    oferecer horario ocupado, mas ele consulta e decide em Python - se
    dois clientes confirmarem no mesmo instante, os dois passam. Quem
    barra de verdade e o banco.
    """

    def test_banco_recusa_dois_agendamentos_no_mesmo_horario(self):
        self.agendar(9)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.agendar(9)

    def test_banco_recusa_sobreposicao_parcial(self):
        uma_hora = Servico.objects.create(
            profissional=self.profissional, nome="Uma hora", duracao_minutos=60
        )
        self.agendar(9, servico=uma_hora)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.agendar(9, 30, servico=uma_hora)

    def test_encostar_no_fim_e_aceito(self):
        self.agendar(9)
        self.agendar(9, 30)
        self.assertEqual(Agendamento.objects.count(), 2)

    def test_cancelado_libera_o_horario_no_banco(self):
        agendamento = self.agendar(9)
        agendamento.status = Agendamento.Status.CANCELADO
        agendamento.save()
        self.agendar(9)
        self.assertEqual(Agendamento.objects.count(), 2)

    def test_fim_e_calculado_pela_duracao_do_servico(self):
        # Nao passo o fim: o modelo calcula sozinho a partir do servico.
        com_intervalo = Servico.objects.create(
            profissional=self.profissional, nome="Com intervalo",
            duracao_minutos=30, intervalo_apos_minutos=15,
        )
        agendamento = self.agendar(9, servico=com_intervalo)
        self.assertEqual(agendamento.fim, self.momento(9, 45))


class TaxaDeFalta(BaseAgenda):
    """O indicador que mede se o sistema resolveu o problema."""

    def test_calcula_o_percentual(self):
        self.agendar(8, status=Agendamento.Status.CONCLUIDO)
        self.agendar(9, status=Agendamento.Status.CONCLUIDO)
        self.agendar(10, status=Agendamento.Status.CONCLUIDO)
        self.agendar(11, status=Agendamento.Status.FALTOU)

        indicadores = taxa_de_falta(self.profissional)
        self.assertEqual(indicadores["concluidos"], 3)
        self.assertEqual(indicadores["faltas"], 1)
        self.assertEqual(indicadores["total"], 4)
        self.assertEqual(indicadores["percentual"], 25.0)

    def test_cancelado_fica_de_fora_da_conta(self):
        # Cancelar e avisar; faltar e sumir. Sao coisas diferentes e
        # misturar as duas estragaria o indicador.
        self.agendar(8, status=Agendamento.Status.CONCLUIDO)
        self.agendar(9, status=Agendamento.Status.CANCELADO)
        self.agendar(10, status=Agendamento.Status.FALTOU)

        indicadores = taxa_de_falta(self.profissional)
        self.assertEqual(indicadores["total"], 2)
        self.assertEqual(indicadores["percentual"], 50.0)

    def test_sem_atendimento_nenhum_o_percentual_e_none(self):
        indicadores = taxa_de_falta(self.profissional)
        self.assertEqual(indicadores["total"], 0)
        self.assertIsNone(indicadores["percentual"])
