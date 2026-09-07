/* =========================================================================
   AgendaJá — comportamentos da interface

   Princípio adotado: o sistema funciona sem JavaScript. Os formulários são
   HTML puro, a escolha de horário é um grupo de radio buttons e a navegação
   é feita por links. O que está aqui melhora a experiência, mas nada
   essencial depende deste arquivo.
   ========================================================================= */

(function () {
    "use strict";

    /* ---------------------------------------------------------------------
       1. Copiar o link público de agendamento
       --------------------------------------------------------------------- */

    function iniciarCopiaDeLink() {
        var botao = document.querySelector("[data-copiar]");
        if (!botao) {
            return;
        }

        botao.addEventListener("click", function () {
            var campo = document.getElementById(botao.dataset.copiar);
            if (!campo) {
                return;
            }

            campo.select();
            campo.setSelectionRange(0, 99999); // iOS

            var textoOriginal = botao.textContent;

            function avisar(mensagem) {
                botao.textContent = mensagem;
                setTimeout(function () {
                    botao.textContent = textoOriginal;
                }, 1800);
            }

            if (navigator.clipboard) {
                navigator.clipboard.writeText(campo.value).then(
                    function () { avisar("Copiado!"); },
                    function () { avisar("Copie manualmente"); }
                );
            } else {
                // Navegadores antigos e páginas servidas sem HTTPS.
                document.execCommand("copy");
                avisar("Copiado!");
            }
        });
    }


    /* ---------------------------------------------------------------------
       2. Máscara de telefone

       O cliente digita como está acostumado — (77) 99999-8888 — e o servidor
       normaliza para E.164 (+5577999998888), único formato aceito pela API
       do WhatsApp. A máscara aqui é conveniência visual; a conversão de
       verdade acontece no servidor, que não confia no que vem do navegador.
       --------------------------------------------------------------------- */

    function aplicarMascaraTelefone(valor) {
        var digitos = valor.replace(/\D/g, "").slice(0, 11);

        if (digitos.length === 0) {
            return "";
        }
        if (digitos.length <= 2) {
            return "(" + digitos;
        }
        if (digitos.length <= 6) {
            return "(" + digitos.slice(0, 2) + ") " + digitos.slice(2);
        }
        if (digitos.length <= 10) {
            // Fixo: (77) 3451-2233
            return "(" + digitos.slice(0, 2) + ") " + digitos.slice(2, 6) +
                   "-" + digitos.slice(6);
        }
        // Celular: (77) 99999-8888
        return "(" + digitos.slice(0, 2) + ") " + digitos.slice(2, 7) +
               "-" + digitos.slice(7);
    }

    function iniciarMascarasDeTelefone() {
        var campos = document.querySelectorAll('input[type="tel"]');

        Array.prototype.forEach.call(campos, function (campo) {
            campo.addEventListener("input", function () {
                var posicaoFinal = campo.selectionStart === campo.value.length;
                campo.value = aplicarMascaraTelefone(campo.value);
                if (posicaoFinal) {
                    campo.setSelectionRange(campo.value.length, campo.value.length);
                }
            });

            // Aplica também ao valor que já veio preenchido pelo servidor.
            if (campo.value) {
                campo.value = aplicarMascaraTelefone(campo.value);
            }
        });
    }


    /* ---------------------------------------------------------------------
       3. Confirmação em ações destrutivas

       Registrar falta e cancelar são ações que o profissional não desfaz
       com um clique. Um data-confirmar no link basta para pedir confirmação.
       --------------------------------------------------------------------- */

    function iniciarConfirmacoes() {
        document.addEventListener("click", function (evento) {
            var alvo = evento.target.closest("[data-confirmar]");
            if (!alvo) {
                return;
            }
            if (!window.confirm(alvo.dataset.confirmar)) {
                evento.preventDefault();
            }
        });
    }


    /* ---------------------------------------------------------------------
       4. Fechar avisos automaticamente

       Mensagens de sucesso somem sozinhas; erros permanecem até o usuário
       fechar, porque costumam exigir alguma ação dele.
       --------------------------------------------------------------------- */

    function iniciarFechamentoDeAvisos() {
        var avisos = document.querySelectorAll(".alert-success");

        Array.prototype.forEach.call(avisos, function (aviso) {
            setTimeout(function () {
                if (window.bootstrap && window.bootstrap.Alert) {
                    window.bootstrap.Alert.getOrCreateInstance(aviso).close();
                } else {
                    aviso.remove();
                }
            }, 5000);
        });
    }


    /* ---------------------------------------------------------------------
       5. Rolar a régua de dias até o dia selecionado

       Quando o cliente escolhe uma data mais adiante, a régua horizontal
       precisa reposicionar para que a seleção fique visível.
       --------------------------------------------------------------------- */

    function iniciarRolagemDaRegua() {
        var selecionado = document.querySelector(".cartao-dia.selecionado");
        if (!selecionado) {
            return;
        }
        var regua = selecionado.closest(".regua-dias");
        if (!regua) {
            return;
        }

        var deslocamento = selecionado.offsetLeft
                         - (regua.clientWidth / 2)
                         + (selecionado.clientWidth / 2);
        regua.scrollLeft = Math.max(0, deslocamento);
    }


    /* ---------------------------------------------------------------------
       6. Evitar envio duplicado do formulário

       Sem isso, um duplo clique em "Confirmar agendamento" dispara duas
       requisições. A segunda seria recusada pela restrição de exclusão do
       banco, mas o cliente veria uma mensagem de erro sem entender o motivo.
       --------------------------------------------------------------------- */

    function iniciarProtecaoContraEnvioDuplo() {
        var formularios = document.querySelectorAll("form[method='post']");

        Array.prototype.forEach.call(formularios, function (formulario) {
            formulario.addEventListener("submit", function () {
                var botao = formulario.querySelector("button[type='submit']");
                if (!botao) {
                    return;
                }
                setTimeout(function () {
                    botao.disabled = true;
                    botao.dataset.textoOriginal = botao.textContent;
                    botao.textContent = "Enviando...";
                }, 0);
            });
        });
    }


    /* ---------------------------------------------------------------------
       Inicialização
       --------------------------------------------------------------------- */

    document.addEventListener("DOMContentLoaded", function () {
        iniciarCopiaDeLink();
        iniciarMascarasDeTelefone();
        iniciarConfirmacoes();
        iniciarFechamentoDeAvisos();
        iniciarRolagemDaRegua();
        iniciarProtecaoContraEnvioDuplo();
    });

})();