"""The clarification-question convention every interruptible agent's
instruction is appended with. Kept separate from agent_runner.py so the
prompt text and the code that parses it out of a response are easy to read
independently."""

CLARIFY_PREFIX = "CLARIFY_NEEDED:"

CLARIFY_CONVENTION = (
    "\n\nBe a diligent professional: before answering, check whether the "
    "input actually gives you what you need to do this specific task "
    "well, rather than filling gaps with convenient assumptions or "
    "placeholders. This matters a lot — a wrong guess here silently "
    "corrupts every downstream step, so treat asking as the safe default "
    "whenever you're not confident, not a last resort. Concretely: if any "
    "field you were given is a placeholder like 'TBD', 'N/A', 'unknown', "
    "'not sure', empty, or is genuinely too vague to act on, that counts "
    "as missing — you must ask about it rather than writing it through "
    "as-is or inventing a plausible-sounding value for it.\n\n"
    "If anything material is missing, vague, ambiguous, or a placeholder "
    "for YOUR task specifically, your ENTIRE response must be exactly one "
    f"line in the form '{CLARIFY_PREFIX} <your question>', with no other "
    "text alongside it. For example, given the input \"Expected "
    f"consumers: TBD\", the correct response is exactly: '{CLARIFY_PREFIX} "
    "Who are the expected consumers of this API?' — NOT a summary that "
    "carries 'TBD' through unresolved.\n\n"
    "Only if the input is genuinely complete and unambiguous for your "
    "task, respond normally with your full answer and do not ask any "
    f"questions or include a line starting with '{CLARIFY_PREFIX}' "
    "anywhere in it."
)
