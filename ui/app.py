"""Streamlit entrypoint.

Chat on the left, a live agent trace panel on the right (steps, tools
called, inputs, outputs), a sidebar to pick a synthetic applicant and
toggle tool-failure injection, and a parsed Verified/Assumed/Simulated/
Next step breakdown of every answer. Function over polish: this is a demo
harness for agent.orchestrator.run_turn, not a production chat UI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from agent.orchestrator import run_turn
from schemas import CaseState
from tools.applicant_lookup import load_applicants

SECTION_NAMES = ["Verified", "Assumed", "Simulated", "Next step"]


def parse_sections(draft: str) -> dict[str, str]:
    """Pull out the four required sections from a draft answer, if present."""
    sections: dict[str, str] = {}
    for name in SECTION_NAMES:
        others = "|".join(n.replace(" ", r"\s+") for n in SECTION_NAMES if n != name)
        pattern = re.compile(
            rf"{name.replace(' ', r'\s+')}\s*:\s*(.*?)(?=\n\s*(?:{others})\s*:|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(draft)
        if match:
            sections[name] = match.group(1).strip()
    return sections


def render_answer(draft: str) -> None:
    sections = parse_sections(draft)
    if sections.get("Verified"):
        st.success(f"**Verified**\n\n{sections['Verified']}")
    if sections.get("Assumed"):
        st.warning(f"**Assumed**\n\n{sections['Assumed']}")
    if sections.get("Simulated"):
        st.info(f"**Simulated**\n\n{sections['Simulated']}")
    if sections.get("Next step"):
        st.markdown(f"**Next step**\n\n{sections['Next step']}")
    if not sections:
        st.write(draft)
    with st.expander("Raw response"):
        st.text(draft)


def init_session_state() -> None:
    st.session_state.setdefault("case_state", CaseState())
    st.session_state.setdefault("conversation", [])
    st.session_state.setdefault("chat_display", [])
    st.session_state.setdefault("inject_failures", False)
    st.session_state.setdefault("pending_message", None)


st.set_page_config(page_title="Certificate Equivalency Agent", layout="wide")
init_session_state()

with st.sidebar:
    st.header("Synthetic Applicant")
    try:
        applicants = load_applicants()
    except Exception as exc:
        # A broken data/applicants.json shouldn't take the whole UI down with it --
        # the chat and trace panels below should still render.
        st.error(f"Could not load synthetic applicants: {exc}")
        applicants = ()

    if applicants:
        labels = {f"{a.applicant_id} — {a.name} ({a.country_of_origin})": a for a in applicants}
        selected_label = st.selectbox("Pick an applicant", list(labels.keys()))
        selected = labels[selected_label]
        st.caption(f"Curriculum on file: {selected.stated_curriculum}")
        st.caption(f"Expected outcome (for testing): {selected.expected_outcome}")

        if st.button("Load applicant into chat"):
            st.session_state.pending_message = (
                f"Hi, I'm applying for certificate equivalency. My applicant ID is {selected.applicant_id}."
            )

    st.divider()
    st.toggle(
        "Inject tool failures",
        key="inject_failures",
        help="Forces simulate_failure=True on every tool call this turn, to demo the retry-then-escalate path.",
    )

    st.divider()
    if st.button("Reset conversation"):
        st.session_state.case_state = CaseState()
        st.session_state.conversation = []
        st.session_state.chat_display = []
        st.rerun()

chat_col, trace_col = st.columns([2, 1])

with chat_col:
    st.subheader("Chat")
    for role, content in st.session_state.chat_display:
        with st.chat_message(role):
            if role == "assistant":
                render_answer(content)
            else:
                st.write(content)

    typed_message = st.chat_input("Type a message...")
    message_to_send = typed_message or st.session_state.pending_message
    st.session_state.pending_message = None

    if message_to_send:
        st.session_state.chat_display.append(("user", message_to_send))
        with st.spinner("Agent working..."):
            try:
                answer = run_turn(
                    st.session_state.case_state,
                    st.session_state.conversation,
                    message_to_send,
                    inject_failures=st.session_state.inject_failures,
                )
            except RuntimeError as exc:
                # config.get_settings() raises this for missing .env vars -- safe to
                # show verbatim, it never contains secrets, only variable names.
                answer = (
                    "Verified: None.\nAssumed: None.\nSimulated: None.\n"
                    f"Next step: The agent failed to run ({exc})."
                )
            except Exception:
                # Any other exception (e.g. a raw Azure/openai SDK error) could embed
                # request/response details -- never interpolate it into a persisted,
                # shareable chat message (CLAUDE.md rule 4).
                answer = (
                    "Verified: None.\nAssumed: None.\nSimulated: None.\n"
                    "Next step: The agent hit an unexpected error. Check the terminal running "
                    "Streamlit for details, then try again."
                )
        st.session_state.chat_display.append(("assistant", answer))
        st.rerun()

with trace_col:
    st.subheader("Agent Trace")
    history = st.session_state.case_state.tool_history
    if not history:
        st.caption("No tool calls yet this session.")
    for entry in reversed(history):
        with st.expander(f"Step {entry.step}: {entry.tool}", expanded=False):
            st.caption(entry.timestamp.isoformat())
            st.markdown("**Input**")
            st.json(entry.args)
            st.markdown("**Output**")
            st.code(entry.result_summary)

    st.divider()
    st.subheader("Case State")
    case_state = st.session_state.case_state
    st.markdown(f"**Applicant:** {case_state.applicant_id or '_none yet_'}")
    st.markdown(f"**Curriculum:** {case_state.curriculum or '_none yet_'}")
    if case_state.open_questions:
        st.markdown("**Open questions**")
        for q in case_state.open_questions:
            st.markdown(f"- {q}")
    if case_state.assumptions:
        st.markdown("**Assumptions made**")
        for a in case_state.assumptions:
            st.markdown(f"- {a}")
    with st.expander("Full case state (JSON)"):
        st.json(case_state.model_dump(mode="json"))
