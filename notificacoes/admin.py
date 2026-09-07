from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Notificacao


@admin.register(Notificacao)
class NotificacaoAdmin(admin.ModelAdmin):
    list_display = [
        "agendada_para", "tipo", "telefone",
        "situacao", "canal", "tentativas",
    ]
    list_filter = ["situacao", "tipo", "canal"]
    search_fields = ["telefone", "conteudo", "id_mensagem"]
    date_hierarchy = "agendada_para"
    readonly_fields = ["criada_em", "enviada_em", "conteudo", "id_mensagem"]

    fieldsets = (
        (None, {"fields": ("agendamento", "tipo", "telefone", "template")}),
        (_("Conteúdo"), {"fields": ("conteudo",)}),
        (_("Envio"), {"fields": (
            "situacao", "canal", "agendada_para", "enviada_em",
            "id_mensagem", "tentativas", "erro",
        )}),
        (_("Controle"), {"fields": ("criada_em",)}),
    )

    actions = ["reenfileirar"]

    @admin.action(description=_("Reenfileirar para novo envio"))
    def reenfileirar(self, request, queryset):
        total = queryset.update(
            situacao=Notificacao.Situacao.PENDENTE, tentativas=0, erro=""
        )
        self.message_user(request, _("%d notificação(ões) reenfileirada(s).") % total)