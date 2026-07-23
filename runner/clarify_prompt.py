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
    "for YOUR task specifically, end your response with exactly one line "
    f"in the form '{CLARIFY_PREFIX} <your question>', as the LAST line of "
    "your response. For example, given the input \"Expected consumers: "
    f"TBD\", a correct response is exactly: '{CLARIFY_PREFIX} Who are the "
    "expected consumers of this API?' — NOT a summary that carries 'TBD' "
    "through unresolved.\n\n"
    "This is a real back-and-forth, not a one-shot form: if the user's "
    "reply to your question is itself a question — asking what you mean, "
    "for an example, or for more context — that is not an answer, and you "
    "must not just repeat your original question verbatim as if you "
    "didn't see their reply. Treat it like a normal conversation: answer "
    "their question helpfully and specifically in a sentence or two, THEN "
    f"end your response with the '{CLARIFY_PREFIX} <question>' line again "
    "(rephrased if that helps) to re-ask for what you still need. Only "
    "once the user's reply actually supplies the missing information "
    "should you drop the marker and give your real, final answer.\n\n"
    "Only if the input is genuinely complete and unambiguous for your "
    "task, respond normally with your full answer and do not include a "
    f"line starting with '{CLARIFY_PREFIX}' anywhere in it."
)
