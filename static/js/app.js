/* AgendaJá — comportamentos da interface.

   O sistema funciona sem JavaScript: os formulários são HTML puro, a
   escolha de horário é um grupo de radio e a navegação é por links. O que
   está aqui melhora a experiência, mas nada essencial depende deste
   arquivo. */

(function () {
    "use strict";

    /* === Copiar o link público de agendamento ============================ */

    function iniciarCopiaDeLink() {
        var botoes = document.querySelectorAll("[data-copiar]");

        Array.prototype.forEach.call(botoes, function (botao) {
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
                    // Navegador antigo ou página servida sem HTTPS.
                    document.execCommand("copy");
                    avisar("Copiado!");
                }
            });
        });
    }


    /* === Máscara de telefone =============================================
       O cliente digita como está acostumado e o servidor converte para
       E.164 (+5577999998888), o formato que o WhatsApp exige. A máscara
       aqui é conveniência: a conversão de verdade é no servidor, que não
       confia no que vem do navegador. */

    function digitosLocais(valor) {
        var digitos = valor.replace(/\D/g, "");

        // Quando o campo já vem preenchido, ele vem em E.164 - e o 55 da
        // frente tem que sair antes de mascarar. Sem isso o 55 virava o
        // DDD, o número do cliente aparecia errado na tela e voltava
        // errado pro banco.
        //
        // Comparo pelo tamanho e não só pelo começo porque DDD 55 existe
        // (Santa Maria): "(55) 99999-8888" tem 11 dígitos e não pode ser
        // tocado.
        if (digitos.length > 11 && digitos.indexOf("55") === 0) {
            digitos = digitos.slice(2);
        }

        return digitos.slice(0, 11);
    }

    function aplicarMascaraTelefone(valor) {
        var digitos = digitosLocais(valor);

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

            if (campo.value) {
                campo.value = aplicarMascaraTelefone(campo.value);
            }
        });
    }


    /* === Confirmação em ações destrutivas ================================
       Registrar falta e cancelar não se desfazem com um clique. Um
       data-confirmar no link basta para pedir confirmação. */

    function iniciarConfirmacoes() {
        document.addEventListener("click", function (evento) {
            if (!evento.target || !evento.target.closest) {
                return;
            }
            var alvo = evento.target.closest("[data-confirmar]");
            if (!alvo) {
                return;
            }
            if (!window.confirm(alvo.dataset.confirmar)) {
                evento.preventDefault();
            }
        });
    }


    /* === Fechar avisos de sucesso ========================================
       Erro fica na tela até a pessoa fechar, porque costuma exigir alguma
       ação dela. */

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


    /* === Rolar a régua de dias até o dia selecionado ===================== */

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


    /* === Evitar envio duplicado ==========================================
       Sem isso, um duplo clique em "Confirmar agendamento" dispara duas
       requisições. A segunda seria recusada pela restrição do banco, mas o
       cliente veria um erro sem entender o motivo.

       O setTimeout de zero é o truque: desabilitar o botão durante o
       submit impediria o navegador de enviar o valor dele. Assim o
       bloqueio só acontece depois que os dados já saíram. */

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
                    botao.textContent = "Enviando...";
                }, 0);
            });
        });
    }


    document.addEventListener("DOMContentLoaded", function () {
        iniciarCopiaDeLink();
        iniciarMascarasDeTelefone();
        iniciarConfirmacoes();
        iniciarFechamentoDeAvisos();
        iniciarRolagemDaRegua();
        iniciarProtecaoContraEnvioDuplo();
    });

})();