import json
import os
from typing import Iterator, Optional

import requests
import streamlit as st

st.set_page_config(page_title="Zenith Air Assistant", page_icon="✈️")

_API_BASE_URL = os.environ.get("SKYOPS_API_BASE_URL", "http://localhost:8000")
_API_KEY = os.environ.get("SKYOPS_API_KEY", "")


def _stream_message(message: str, thread_id: Optional[str], status_box, result: dict) -> Iterator[str]:
    """Calls the backend's POST /chat/stream (SSE) - the frontend never talks to the graph/
    gateway directly. "status" events update `status_box` in place (real graph progress, not a
    decorative animation); "token" events are yielded for st.write_stream to render as the
    answer types out; the "done" event's thread_id/pending_approval are written into `result`.
    The backend only ever streams the final, guardrail-approved reply - never raw LLM output -
    so nothing shown here has bypassed output_guard."""
    response = requests.post(
        f"{_API_BASE_URL}/chat/stream",
        json={"message": message, "thread_id": thread_id},
        headers={"X-API-Key": _API_KEY},
        stream=True,
        timeout=60,
    )
    response.raise_for_status()

    event = None
    first_token = True
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data = json.loads(line[len("data:") :].strip())
            if event == "status":
                status_box.markdown(f"_{data['text']}_")
            elif event == "token":
                if first_token:
                    status_box.empty()
                    first_token = False
                yield data["text"]
            elif event == "done":
                result.update(data)


def _error_detail(exc: requests.HTTPError) -> str:
    if exc.response is None:
        return str(exc)
    try:
        return exc.response.json().get("detail", exc.response.text)
    except ValueError:
        return exc.response.text


def _get_chat_history(thread_id: str) -> dict:
    """GET /chat/history/{thread_id} - the authoritative message list from the backend, not
    whatever this browser session happens to have cached. Needed because another actor (a staff
    member resuming an approval from the Staff Approval page) can append messages to the same
    thread out-of-band - this client only finds out by asking, not by any push notification."""
    response = requests.get(
        f"{_API_BASE_URL}/chat/history/{thread_id}", headers={"X-API-Key": _API_KEY}, timeout=15
    )
    response.raise_for_status()
    return response.json()


def _staff_login(username: str, password: str) -> dict:
    """POST /ops/auth/login - not gated by X-API-Key, staff prove identity with their own
    username/password (checked against the staff_users SQLite table) instead."""
    response = requests.post(
        f"{_API_BASE_URL}/ops/auth/login", json={"username": username, "password": password}, timeout=15
    )
    response.raise_for_status()
    return response.json()


def _staff_logout(token: str) -> None:
    requests.post(f"{_API_BASE_URL}/ops/auth/logout", headers={"Authorization": f"Bearer {token}"}, timeout=15)


def _ops_headers(token: str) -> dict:
    return {"X-API-Key": _API_KEY, "Authorization": f"Bearer {token}"}


def _list_pending_approvals(token: str) -> list[dict]:
    response = requests.get(f"{_API_BASE_URL}/ops/approvals", headers=_ops_headers(token), timeout=15)
    response.raise_for_status()
    return response.json()


def _decide_approval(token: str, thread_id: str, approved: bool, agent_note: Optional[str]) -> dict:
    response = requests.post(
        f"{_API_BASE_URL}/ops/approvals/{thread_id}/decision",
        json={"approved": approved, "agent_note": agent_note},
        headers=_ops_headers(token),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _get_rag_eval_results(token: str) -> dict:
    """GET /ops/evals/rag - the last saved evals/run_ragas_eval.py snapshot, not a live re-run
    (that needs ragas/pytest, dev-only deps, plus a live litellm-proxy - see app/routes/evals.py)."""
    response = requests.get(f"{_API_BASE_URL}/ops/evals/rag", headers=_ops_headers(token), timeout=15)
    response.raise_for_status()
    return response.json()


if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "staff_session" not in st.session_state:
    st.session_state.staff_session = None

# Role-based nav: Customer Chat needs no login and is always the default landing page. Once
# logged in, the available pages depend on role - "staff" only sees Staff Approval (their one
# job), "admin" sees everything including the Eval Dashboard (which also requires admin
# server-side - see middleware/auth.py's _has_admin_access - this list is UX, not the real gate).
_ANON_PAGES = ["Customer Chat", "Staff Login"]
_STAFF_PAGES = ["Staff Approval"]
_ADMIN_PAGES = ["Customer Chat", "Staff Approval", "Eval Dashboard"]

if "nav_page" not in st.session_state:
    st.session_state.nav_page = _ANON_PAGES[0]

if st.session_state.staff_session is None:
    available_pages = _ANON_PAGES
elif st.session_state.staff_session["role"] == "admin":
    available_pages = _ADMIN_PAGES
else:
    available_pages = _STAFF_PAGES

if st.session_state.nav_page not in available_pages:
    st.session_state.nav_page = available_pages[0]

# Sidebar nav is plain st.button per page (not st.radio) so each one can be styled as a
# selectable "tab": type="primary" on the active page picks up the dark-background/light-text
# override below, type="secondary" leaves the rest as plain outlined buttons.
st.markdown(
    """
    <style>
    [data-testid="stSidebar"] .stButton button {
        padding-top: 0.75rem;
        padding-bottom: 0.75rem;
    }
    [data-testid="stSidebar"] .stButton button[kind="primary"],
    [data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {
        background-color: #1e1e1e;
        color: #f5f5f5;
        border-color: #1e1e1e;
    }
    [data-testid="stSidebar"] .stButton button[kind="primary"]:hover,
    [data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:hover {
        background-color: #333333;
        color: #ffffff;
        border-color: #333333;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.caption(f"Backend: {_API_BASE_URL}")
    for page in available_pages:
        if st.button(
            page,
            key=f"nav_{page}",
            type="primary" if st.session_state.nav_page == page else "secondary",
            width="stretch",
        ):
            st.session_state.nav_page = page
            st.rerun()

    if st.session_state.staff_session is not None:
        session = st.session_state.staff_session
        st.divider()
        st.caption(f"Logged in as **{session['username']}** ({session['role']})")
        if st.button("Log out", width="stretch"):
            _staff_logout(session["token"])
            st.session_state.staff_session = None
            st.session_state.nav_page = _ANON_PAGES[0]
            st.rerun()

    if st.session_state.nav_page == "Customer Chat" and st.button("New conversation"):
        st.session_state.thread_id = None
        st.session_state.messages = []
        st.rerun()

nav = st.session_state.nav_page

st.title("Zenith Air Assistant")

if not _API_KEY:
    st.error("SKYOPS_API_KEY is not set - see frontend/.env.example.")
    st.stop()

# Called at the top level (not inside a column/expander/other container) so Streamlit pins it
# to the bottom of the page - nested inside one, it renders inline instead, which is what
# caused the "chat started from below the textbox" layout bug (docs/progress.md). A plain `if`
# doesn't create a container, so gating it on the current page is still safe here.
prompt = st.chat_input("Type your message...") if nav == "Customer Chat" else None

if nav == "Customer Chat":
    st.caption("Ask about flight status, booking disruptions, or airline policy.")

    if st.session_state.thread_id:
        if st.button("Refresh", help="Check for updates - e.g. a staff decision on a pending request"):
            st.rerun()
        try:
            history = _get_chat_history(st.session_state.thread_id)
        except requests.RequestException:
            pass  # keep showing the last-known local history rather than blanking the page
        else:
            st.session_state.messages = [
                {"role": m["role"], "content": m["content"]} for m in history["messages"]
            ]
            if history.get("pending_approval"):
                st.info("This request is still awaiting a Zenith Air agent's sign-off.")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            status_box = st.empty()
            result: dict = {}
            try:
                full_reply = st.write_stream(
                    _stream_message(prompt, st.session_state.thread_id, status_box, result)
                )
            except requests.HTTPError as exc:
                status_box.empty()
                st.error(f"SkyOps couldn't process that ({exc.response.status_code}): {_error_detail(exc)}")
            except requests.RequestException as exc:
                status_box.empty()
                st.error(f"Couldn't reach the SkyOps backend: {exc}")
            else:
                status_box.empty()
                st.session_state.thread_id = result.get("thread_id")
                if result.get("pending_approval"):
                    st.info("This request needs a Zenith Air agent's sign-off before it's finalized.")
                st.session_state.messages.append({"role": "assistant", "content": full_reply})

elif nav == "Staff Login":
    st.caption("Sign in with your Zenith Air staff or admin account.")
    with st.form("staff_login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        try:
            session = _staff_login(username, password)
        except requests.HTTPError as exc:
            st.error(f"Login failed: {_error_detail(exc)}")
        except requests.RequestException as exc:
            st.error(f"Couldn't reach the SkyOps backend: {exc}")
        else:
            st.session_state.staff_session = session
            st.rerun()

elif nav == "Staff Approval":
    st.caption("Review and decide on booking-disruption compensation requests awaiting sign-off.")
    session = st.session_state.staff_session

    if st.button("Refresh queue"):
        st.rerun()

    try:
        approvals = _list_pending_approvals(session["token"])
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (401, 403):
            st.warning("Your session has expired - please log in again.")
            st.session_state.staff_session = None
        else:
            st.error(f"Couldn't load approvals ({exc.response.status_code}): {_error_detail(exc)}")
        approvals = []
    except requests.RequestException as exc:
        st.error(f"Couldn't reach the SkyOps backend: {exc}")
        approvals = []

    if not approvals:
        st.info("No pending approvals.")
    for approval in approvals:
        thread_id = approval["thread_id"]
        title = f"PNR {approval['pnr']} - flight {approval['flight_number']} - {approval['compensation_usd']} USD"
        with st.expander(title):
            st.write(approval["reason"])
            st.caption(f"Requested: {approval['created_at']}")
            note = st.text_input(
                "Agent note (internal only - never shown to the passenger)", key=f"note_{thread_id}"
            )
            approve_col, reject_col = st.columns(2)
            with approve_col:
                if st.button("Approve", key=f"approve_{thread_id}"):
                    try:
                        _decide_approval(session["token"], thread_id, True, note or None)
                    except requests.HTTPError as exc:
                        st.error(f"Couldn't approve ({exc.response.status_code}): {_error_detail(exc)}")
                    except requests.RequestException as exc:
                        st.error(f"Couldn't reach the SkyOps backend: {exc}")
                    else:
                        st.rerun()
            with reject_col:
                if st.button("Reject", key=f"reject_{thread_id}"):
                    try:
                        _decide_approval(session["token"], thread_id, False, note or None)
                    except requests.HTTPError as exc:
                        st.error(f"Couldn't reject ({exc.response.status_code}): {_error_detail(exc)}")
                    except requests.RequestException as exc:
                        st.error(f"Couldn't reach the SkyOps backend: {exc}")
                    else:
                        st.rerun()

elif nav == "Eval Dashboard":
    st.caption("Latest RAG quality eval (docs/progress.md's Day 7 ragas eval) - a saved snapshot from the last `evals/run_ragas_eval.py` run, not a live re-run.")
    session = st.session_state.staff_session

    if st.button("Refresh", key="refresh_evals"):
        st.rerun()

    try:
        results = _get_rag_eval_results(session["token"])
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            st.info(_error_detail(exc))
        elif exc.response is not None and exc.response.status_code in (401, 403):
            st.warning("Your session has expired - please log in again.")
            st.session_state.staff_session = None
        else:
            st.error(f"Couldn't load eval results ({exc.response.status_code}): {_error_detail(exc)}")
    except requests.RequestException as exc:
        st.error(f"Couldn't reach the SkyOps backend: {exc}")
    else:
        st.caption(f"Generated: {results['generated_at']}")
        if results["passed"]:
            st.success("All thresholds met.")
        else:
            st.error("Below threshold - see per-question scores below.")

        avg = results["averages"]
        thresholds = results["thresholds"]
        metric_col1, metric_col2 = st.columns(2)
        with metric_col1:
            st.metric(
                "Faithfulness",
                f"{avg['faithfulness']:.2f}",
                delta=f"{avg['faithfulness'] - thresholds['faithfulness']:+.2f} vs threshold",
            )
        with metric_col2:
            st.metric(
                "Context precision",
                f"{avg['context_precision']:.2f}",
                delta=f"{avg['context_precision'] - thresholds['context_precision']:+.2f} vs threshold",
            )

        st.dataframe(results["questions"], width="stretch", hide_index=True)
