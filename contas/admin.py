from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from .models import AreaAtuacao, Local, Profissional, Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    """Admin ajustado para autenticação por e-mail (sem ``username``)."""

    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "is_staff", "is_active"]
    list_filter = ["is_staff", "is_superuser", "is_active"]
    search_fields = ["email", "first_name", "last_name"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Dados pessoais"), {"fields": ("first_name", "last_name")}),
        (
            _("Permissões"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (_("Datas importantes"), {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )


@admin.register(AreaAtuacao)
class AreaAtuacaoAdmin(admin.ModelAdmin):
    list_display = ["nome", "slug", "ativo"]
    list_filter = ["ativo"]
    search_fields = ["nome"]
    prepopulated_fields = {"slug": ("nome",)}


class LocalInline(admin.TabularInline):
    model = Local
    extra = 1


@admin.register(Profissional)
class ProfissionalAdmin(admin.ModelAdmin):
    list_display = ["nome_publico", "area", "telefone", "ativo", "criado_em"]
    list_filter = ["area", "ativo"]
    search_fields = ["nome_publico", "usuario__email", "telefone"]
    prepopulated_fields = {"slug": ("nome_publico",)}
    autocomplete_fields = ["usuario"]
    inlines = [LocalInline]

    fieldsets = (
        (None, {"fields": ("usuario", "nome_publico", "slug", "area", "ativo")}),
        (_("Contato"), {"fields": ("telefone", "apresentacao")}),
        (
            _("Regras da agenda"),
            {
                "fields": (
                    "granularidade_minutos",
                    "antecedencia_minima_horas",
                    "antecedencia_maxima_dias",
                ),
                "description": _(
                    "Controlam de quantos em quantos minutos os horários são "
                    "ofertados e a janela em que o cliente pode agendar."
                ),
            },
        ),
    )


@admin.register(Local)
class LocalAdmin(admin.ModelAdmin):
    list_display = ["nome", "profissional", "principal", "ativo"]
    list_filter = ["principal", "ativo"]
    search_fields = ["nome", "profissional__nome_publico"]
