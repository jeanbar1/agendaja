from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Agendamento, Bloqueio, Disponibilidade, Servico


@admin.register(Servico)
class ServicoAdmin(admin.ModelAdmin):
    list_display = [
        "nome",
        "profissional",
        "duracao_minutos",
        "intervalo_apos_minutos",
        "preco",
        "modalidade",
        "ativo",
    ]
    list_filter = ["modalidade", "ativo", "profissional"]
    search_fields = ["nome", "profissional__nome_publico"]


@admin.register(Disponibilidade)
class DisponibilidadeAdmin(admin.ModelAdmin):
    list_display = ["profissional", "dia_semana", "hora_inicio", "hora_fim", "local", "ativo"]
    list_filter = ["dia_semana", "ativo", "profissional"]


@admin.register(Bloqueio)
class BloqueioAdmin(admin.ModelAdmin):
    list_display = ["profissional", "inicio", "fim", "motivo"]
    list_filter = ["profissional"]
    date_hierarchy = "inicio"


@admin.register(Agendamento)
class AgendamentoAdmin(admin.ModelAdmin):
    list_display = ["inicio", "cliente", "servico", "profissional", "status"]
    list_filter = ["status", "profissional", "servico"]
    search_fields = ["cliente__nome", "cliente__telefone"]
    date_hierarchy = "inicio"
    readonly_fields = ["codigo", "criado_em", "atualizado_em"]
    autocomplete_fields = ["cliente"]

    fieldsets = (
        (None, {"fields": ("profissional", "cliente", "servico", "local")}),
        (_("Período"), {"fields": ("inicio", "fim")}),
        (
            _("Situação"),
            {
                "fields": (
                    "status",
                    "confirmado_em",
                    "cancelado_em",
                    "motivo_cancelamento",
                )
            },
        ),
        (_("Outros"), {"fields": ("observacoes", "codigo", "criado_em", "atualizado_em")}),
    )

    actions = ["marcar_como_concluido", "marcar_como_falta"]

    @admin.action(description=_("Marcar como concluído"))
    def marcar_como_concluido(self, request, queryset):
        total = queryset.update(status=Agendamento.Status.CONCLUIDO)
        self.message_user(request, _("%d agendamento(s) concluído(s).") % total)

    @admin.action(description=_("Marcar como falta do cliente"))
    def marcar_como_falta(self, request, queryset):
        total = queryset.update(status=Agendamento.Status.FALTOU)
        self.message_user(request, _("%d falta(s) registrada(s).") % total)
