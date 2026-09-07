from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Cliente, ConsentimentoOptIn, Feedback


class ConsentimentoInline(admin.TabularInline):
    model = ConsentimentoOptIn
    extra = 0
    readonly_fields = ["concedido_em"]


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ["nome", "telefone", "profissional", "consentimento_vigente", "criado_em"]
    list_filter = ["profissional"]
    search_fields = ["nome", "telefone", "email"]
    inlines = [ConsentimentoInline]

    @admin.display(boolean=True, description=_("pode receber mensagem"))
    def consentimento_vigente(self, obj):
        return obj.pode_receber_mensagem


@admin.register(ConsentimentoOptIn)
class ConsentimentoOptInAdmin(admin.ModelAdmin):
    list_display = ["cliente", "origem", "concedido_em", "revogado_em"]
    list_filter = ["origem"]
    search_fields = ["cliente__nome", "cliente__telefone"]
    readonly_fields = ["concedido_em"]


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ["agendamento", "nota", "respondido_em"]
    list_filter = ["nota"]
    readonly_fields = ["respondido_em"]
