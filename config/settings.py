"""
Configurações do projeto AgendaJá.

Sistema Web de Gerenciamento e Notificação para Agendamento de Serviços
por Profissionais Autônomos.

Trabalho de Conclusão de Curso — Análise e Desenvolvimento de Sistemas
Instituto Federal Baiano — Campus Guanambi
"""

from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Segurança
# ---------------------------------------------------------------------------

SECRET_KEY = config("SECRET_KEY")

DEBUG = config("DEBUG", default=False, cast=bool)

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=Csv())

CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())
# Endereços de fora que podem enviar formulário para cá. Preciso disso
# quando exponho o sistema por um túnel (ngrok e parecidos) para o
# WhatsApp alcançar o webhook: sem listar o endereço aqui, todo POST
# volta com erro de CSRF, mesmo com o formulário certo.
#
# Exemplo no .env: CSRF_TRUSTED_ORIGINS=https://abc123.ngrok-free.app
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())


# ---------------------------------------------------------------------------
# Aplicações
# ---------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django.contrib.sites",
]

APPS_LOGIN_SOCIAL = [
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
]

APPS_LOCAIS = [
    "contas",
    "agenda",
    "clientes",
    "notificacoes.apps.NotificacoesConfig",
]

INSTALLED_APPS = DJANGO_APPS + APPS_LOGIN_SOCIAL + APPS_LOCAIS


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Tem que vir depois do de autenticacao.
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
    }
}


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "contas.Usuario"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "painel"
LOGOUT_REDIRECT_URL = "login"


# ---------------------------------------------------------------------------
# Envio de e-mail
# ---------------------------------------------------------------------------
#
# Só uso e-mail em um lugar: o "esqueci minha senha". O padrão do Django
# é falar com um servidor SMTP em localhost, que não existe aqui — a
# tela devolvia erro 500 quando alguém pedia para recuperar a senha.
#
# Com o backend de console a mensagem sai impressa no terminal, que é o
# suficiente para desenvolvimento e para a demonstração. Em produção é
# só apontar para um SMTP de verdade pelo .env.
EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

DEFAULT_FROM_EMAIL = config(
    "DEFAULT_FROM_EMAIL", default="AgendaJá <nao-responda@agendaja.local>"
)


# ---------------------------------------------------------------------------
# Entrar com o Google
# ---------------------------------------------------------------------------
#
# O sistema aceita dois caminhos: e-mail e senha, que e o do proprio
# Django, e a conta do Google. Deixei os dois de proposito - quem nao
# quiser dar acesso a conta do Google continua entrando do jeito normal,
# e quem quiser pula a etapa de inventar mais uma senha.
#
# Quem entra pelo Google cai sem perfil de profissional, porque o Google
# so me da nome e e-mail. Por isso existe a tela de completar cadastro.

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    # O do Django continua valendo: e ele que atende o login por e-mail
    # e senha.
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# Meu usuario nao tem username, so e-mail. Sem essas tres linhas o
# allauth tenta preencher um campo que nao existe e quebra.
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None

# Nao peco confirmacao por e-mail: quem vem do Google ja teve o e-mail
# verificado por eles, e exigir confirmacao aqui so criaria uma barreira
# a mais.
ACCOUNT_EMAIL_VERIFICATION = "none"

# Entra direto, sem a tela intermediaria de "confirme seus dados".
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_LOGIN_ON_GET = True

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        # Deixo a credencial aqui, vinda do .env, em vez de cadastrar no
        # admin. Assim nao preciso lembrar de configurar de novo toda vez
        # que recrio o banco.
        "APP": {
            "client_id": config("GOOGLE_CLIENT_ID", default=""),
            "secret": config("GOOGLE_CLIENT_SECRET", default=""),
            "key": "",
        },
    }
}

# Se as credenciais nao estiverem no .env, o botao do Google nem aparece
# na tela de login.
LOGIN_COM_GOOGLE = bool(config("GOOGLE_CLIENT_ID", default=""))


# ---------------------------------------------------------------------------
# Internacionalização
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "pt-br"

TIME_ZONE = "America/Sao_Paulo"

USE_I18N = True

USE_TZ = True


# ---------------------------------------------------------------------------
# Arquivos estáticos e de mídia
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Notificação por WhatsApp
# ---------------------------------------------------------------------------

WHATSAPP_CANAL = config("WHATSAPP_CANAL", default="simulado")


# --- Evolution API ---
EVOLUTION_URL = config("EVOLUTION_URL", default="")
EVOLUTION_API_KEY = config("EVOLUTION_API_KEY", default="")
EVOLUTION_PREFIXO = config("EVOLUTION_PREFIXO", default="agendaja")

# Antecedência, em horas, do envio do lembrete automático.
LEMBRETE_ANTECEDENCIA_HORAS = config("LEMBRETE_ANTECEDENCIA_HORAS", default=24, cast=int)

# Manda na hora as mensagens que não têm horário marcado: a confirmação
# do agendamento, o aviso para o profissional, o cancelamento e o pedido
# de avaliação. Antes elas ficavam na fila esperando o comando agendado,
# e o cliente terminava de marcar sem receber nada.
#
# Desligar aqui faz tudo voltar a sair só pelo comando.
ENVIO_IMEDIATO = config("ENVIO_IMEDIATO", default=True, cast=bool)


# Endereço público do sistema. Preciso dele porque os links que vão
# dentro da mensagem de WhatsApp têm que ser absolutos - "/avaliar/..."
# não abre nada no celular de ninguém. Em desenvolvimento aponta para o
# runserver; em produção, para o domínio de verdade.
URL_BASE = config("URL_BASE", default="http://127.0.0.1:8000").rstrip("/")