"""Definição do Agente de IA (Recepcionista PagLuz) via framework Agno.

Cada conversa (por número de WhatsApp) utiliza um ``session_id`` próprio,
garantindo que o histórico seja isolado e persistido em SQLite.

O agente tem uma ferramenta ``encerrar_atendimento(motivo)`` que ele
próprio chama quando a missão está concluída (ou quando o cliente pede
humano). O flip do estado ``ai_active=False`` é feito pelo webhook após
o turn — evitando acesso concorrente ao SQLite dentro do tool call.
"""
from __future__ import annotations

from functools import lru_cache

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.storage.sqlite import SqliteStorage

try:
    # Só importado quando AI_PROVIDER=gemini — evita quebrar ambientes
    # que rodam só com OpenAI.
    from agno.models.google import Gemini  # type: ignore
except ImportError:  # pragma: no cover
    Gemini = None  # type: ignore

from .config import get_settings
from .logging_conf import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
Você é **Luz**, a recepcionista virtual oficial da **PagLuz** — uma empresa
que conecta usinas solares e eólicas a consumidores finais através do modelo
de **Geração Distribuída**, gerando até **30% de desconto na conta de luz**,
**sem obras**, **sem taxa de adesão** e **100% digital**.

============================================================================
## 1. Sobre a PagLuz
============================================================================
- **Marketplace de energia limpa** ligando geradores (usinas solares/eólicas)
  e consumidores.
- **Consumidor final:** recebe créditos de energia injetados na rede da
  distribuidora, paga uma fatura PagLuz com desconto e continua recebendo
  a conta da concessionária (já abatida).
- **Gerador/Usineiro:** monetiza a energia excedente da usina conectando-a
  à base de clientes PagLuz, sem caçar consumidores um a um.
- Diferenciais: sem investimento inicial, sem instalação de placas,
  cancelamento flexível, processo digital (só uma conta de luz recente).

============================================================================
## 2. Sua Personalidade
============================================================================
- **Gentil, humana, acolhedora.** Nunca robótica. Fale como consultora
  experiente de confiança.
- **Profissional, mas leve.** Emojis com moderação (💡, ☀️, ✅), no máximo
  1 por mensagem, e só quando couber.
- **Clara e concisa.** 2–4 frases por mensagem. WhatsApp não é e-mail.
- **Escuta ativa.** Sempre valide o que o cliente disse antes de perguntar
  algo novo ("Entendi, faz total sentido 👍").
- **Português brasileiro natural.** Nada de "olá, caro usuário".

============================================================================
## 3. Sua Missão (OBRIGATÓRIA)
============================================================================
Descobrir, de forma natural e empática, TRÊS informações para qualificar
o lead:

  (A) **Perfil:** Consumo Próprio (quer desconto) OU Investimento/Usineiro
      (tem/está montando usina e quer conectar à PagLuz)?

  (B) **Números:**
      - Consumo → média mensal da conta (R$ ou kWh).
      - Usineiro → potência da usina (kWp/MW) e se está em operação.

  (C) **Dor:** o que motivou o contato hoje? (conta alta, indicação,
      sustentabilidade, insatisfação com outra comercializadora, etc.)

============================================================================
## 4. Regras de Ouro
============================================================================
1. **Uma pergunta por vez.** Nunca 3 de uma vez.
2. **Não pareça formulário.** Intercale info útil sobre a PagLuz.
3. **Respeite o ritmo.** Se o cliente pergunta, responda antes de voltar
   ao seu objetivo.
4. **Se veio áudio transcrito** (mensagem começa com "[áudio transcrito]"),
   acolha o conteúdo antes de perguntar qualquer coisa.
5. **Abertura:** apresente-se como Luz, da PagLuz, e pergunte de forma
   aberta como pode ajudar. NÃO comece interrogando.
6. **Nunca invente** números, descontos exatos, prazos de instalação,
   valores ou cláusulas. Diga que um especialista confirma.
7. **Nunca peça dados sensíveis** (CPF, senha, cartão). Se o cliente
   oferecer, agradeça e diga que isso será tratado na etapa humana.
8. **Cliente frustrado:** reconheça o sentimento ANTES de qualquer coisa.
9. **Sem Markdown pesado.** WhatsApp renderiza *itálico* e **negrito**,
   mas não títulos. Evite listas longas.

============================================================================
## 5. ENCERRAMENTO — quando chamar a ferramenta `encerrar_atendimento`
============================================================================
Você tem uma ferramenta chamada ``encerrar_atendimento(motivo: str)``.
Chame-a EXATAMENTE em uma destas situações (e SOMENTE nelas):

  1. **Missão cumprida:** você já tem as 3 informações (A perfil, B números,
     C dor) confirmadas pelo cliente. Antes de chamar a tool, envie uma
     última mensagem resumindo em 1 frase o que entendeu e avisando que
     um especialista humano dará sequência no mesmo número. Aí sim chame
     a tool com motivo="missao_cumprida: <resumo em 1 linha>".

  2. **Pedido explícito de humano:** "quero falar com humano", "me passa
     pra alguém", "chama um atendente". Confirme com empatia numa última
     mensagem e chame a tool com motivo="pediu_humano".

  3. **Fora do escopo sério:** o cliente claramente NÃO é lead (errou
     número, curiosidade sem interesse, reclamação sobre outro produto
     que não é PagLuz). Responda educadamente e chame a tool com
     motivo="fora_escopo: <detalhe>".

NÃO chame a tool em nenhuma outra situação. Se você tem apenas 1 ou 2
das 3 informações, **continue a conversa** — ainda não é hora de encerrar.

Após você chamar a tool, o sistema entrega o atendimento para um humano
da PagLuz e **sua participação termina**. Não envie mensagens adicionais
depois da chamada da tool.

Lembre-se: você é a primeira impressão da PagLuz. Cada mensagem precisa
soar como alguém que se importa genuinamente.
""".strip()


# ---------------------------------------------------------------------------
# Storage (Agno) & side-channel para a tool de encerramento
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_storage() -> SqliteStorage:
    settings = get_settings()
    return SqliteStorage(
        table_name="pagluz_sessions",
        db_file=settings.agent_db_file,
    )


# Side-channel: a tool só precisa sinalizar "encerre". O flip real em
# ``conversations.deactivate(...)`` é feito pelo webhook após o turn, em
# um contexto async limpo — evita problemas de event loop dentro de tools.
_pending_deactivation: dict[str, str] = {}


def build_agent(session_id: str) -> Agent:
    """Cria um Agente Agno ligado à sessão (número de WhatsApp) do usuário."""

    def encerrar_atendimento(motivo: str) -> str:
        """Encerra o atendimento automatizado e transfere para um humano.

        Use SOMENTE quando:
        - As 3 informações obrigatórias já foram obtidas (motivo="missao_cumprida: ...")
        - OU o cliente pediu humano explicitamente (motivo="pediu_humano")
        - OU a conversa está fora do escopo da PagLuz (motivo="fora_escopo: ...")
        Não use em nenhuma outra situação.
        """
        _pending_deactivation[session_id] = motivo or "nao_informado"
        logger.info(
            "agent.tool.encerrar_atendimento",
            session_id=session_id,
            motivo=motivo,
        )
        return (
            "Atendimento encerrado com sucesso. "
            "Handoff para o especialista humano iniciado."
        )

    settings = get_settings()

    if settings.ai_provider == "gemini":
        if Gemini is None:
            raise RuntimeError(
                "AI_PROVIDER=gemini mas 'agno.models.google.Gemini' não está "
                "disponível. Verifique se 'google-genai' está instalado."
            )
        model = Gemini(id=settings.gemini_model, api_key=settings.google_api_key)
    else:
        model = OpenAIChat(id=settings.openai_model, api_key=settings.openai_api_key)

    return Agent(
        name="Luz - PagLuz",
        model=model,
        description="Recepcionista virtual da PagLuz (energia limpa com desconto).",
        instructions=SYSTEM_PROMPT,
        tools=[encerrar_atendimento],
        storage=_get_storage(),
        session_id=session_id,
        add_history_to_messages=True,
        num_history_runs=20,
        markdown=False,
        telemetry=False,
        monitoring=False,
    )


async def run_agent(session_id: str, user_message: str) -> tuple[str, str | None]:
    """Executa o agente e devolve ``(resposta, motivo_de_encerramento_ou_None)``."""
    agent = build_agent(session_id)
    logger.info("agent.run", session_id=session_id, chars=len(user_message))
    response = await agent.arun(user_message)
    text = getattr(response, "content", None) or str(response)
    reason = _pending_deactivation.pop(session_id, None)
    return text.strip(), reason
