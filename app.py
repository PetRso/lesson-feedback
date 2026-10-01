"""Slovenský nástroj na hodnotenie príprav na vyučovaciu hodinu."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

import streamlit as st

from ai_service import LEVEL_META, consult_lesson_plan, evaluate_lesson_plan, extract_text, has_ai_credentials


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

st.set_page_config(
    page_title="Hodnotenie prípravy na vyučovaciu hodinu",
    page_icon=":material/fact_check:",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.html(f"<style>{(APP_DIR / 'styles.css').read_text(encoding='utf-8')}</style>")

try:
    configured_secrets = dict(st.secrets)
except FileNotFoundError:
    configured_secrets = {}
for secret_name in (
    "OPENAI_API_KEY", "OPENAI_MODEL"
):
    if configured_secrets.get(secret_name) and not os.getenv(secret_name):
        os.environ[secret_name] = str(configured_secrets[secret_name])


@st.cache_data
def load_evaluation_sources() -> tuple[str, str, str]:
    config_dir = APP_DIR / "config"
    svp = (config_dir / "sk_svp_feedback.txt").read_text(encoding="utf-8")
    rubric = (config_dir / "rubric.md").read_text(encoding="utf-8")
    system_prompt = (config_dir / "system-prompt.md").read_text(encoding="utf-8")
    return svp, rubric, system_prompt


def init_state() -> None:
    st.session_state.setdefault("lesson_plan", "")
    st.session_state.setdefault("evaluation", None)
    st.session_state.setdefault("consultation", [])
    st.session_state.setdefault("plan_view", "Upraviť text")
    st.session_state.setdefault("last_evaluated_plan", "")


def sync_lesson_plan_from_editor() -> None:
    """Uloží obsah widgetu do stabilného stavu, ktorý prežije jeho skrytie."""
    st.session_state.lesson_plan = st.session_state.get("lesson_plan_editor", "")


def annotated_plan(plan: str, highlights: list[dict]) -> str:
    """Vráti bezpečné HTML s neprekrývajúcimi sa presnými citáciami."""
    occupied: list[tuple[int, int]] = []
    ranges: list[tuple[int, int, dict]] = []
    for item in highlights:
        quote = item.get("quote", "").strip()
        if not quote:
            continue
        for match in re.finditer(re.escape(quote), plan, flags=re.IGNORECASE):
            start, end = match.start(), match.end()
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            occupied.append((start, end))
            ranges.append((start, end, item))
            break
    if not ranges:
        safe_plan = html.escape(plan).replace("\n", "<br>")
        return f'<div class="annotated-plan">{safe_plan}</div>'
    output: list[str] = []
    cursor = 0
    for start, end, item in sorted(ranges, key=lambda value: value[0]):
        output.append(html.escape(plan[cursor:start]))
        meta = LEVEL_META[item["level"]]
        tooltip = html.escape(f'{meta["label"]}: {item.get("reason", "")}', quote=True)
        quote_html = html.escape(plan[start:end])
        output.append(f'<mark class="mark-{item["level"]}" title="{tooltip}">{quote_html}</mark>')
        cursor = end
    output.append(html.escape(plan[cursor:]))
    return f'<div class="annotated-plan">{"".join(output).replace(chr(10), "<br>")}</div>'


def verdict_color(verdict: str) -> str:
    lowered = verdict.lower()
    if lowered.startswith("v súlade"):
        return "green"
    if "v zásade" in lowered:
        return "blue"
    if "čiastočne" in lowered:
        return "orange"
    if lowered.startswith("nie je"):
        return "red"
    return "gray"


def render_feedback(evaluation: dict) -> None:
    st.badge(evaluation["verdict"], color=verdict_color(evaluation["verdict"]), icon=":material/fact_check:")
    st.markdown(evaluation["rationale"])

    st.subheader("Zvýraznené zistenia")
    if not evaluation["highlights"]:
        st.caption("V texte sa nenašli úseky, ktoré by bolo možné spoľahlivo priradiť k úrovni.")
    for item in evaluation["highlights"]:
        meta = LEVEL_META[item["level"]]
        with st.container(border=True):
            st.markdown(f'{meta["icon"]} **{meta["label"]}:** „{item["quote"]}“')
            st.markdown(f'**Prečo:** {item["reason"]}')
            if item["suggestion"]:
                st.markdown(f'**Ako upraviť:** {item["suggestion"]}')

    st.subheader("Časti v súlade")
    if evaluation["strengths"]:
        for strength in evaluation["strengths"]:
            st.markdown(f"- {strength}")
    else:
        st.caption("V texte nie je dostatok dôkazov na pomenovanie konkrétnej silnej stránky.")

    st.subheader("Kontrola väzby")
    for name, status in evaluation["alignment_chain"].items():
        st.markdown(f"**{name}:** {status}")

    st.subheader("Hodnotenie podľa rubriky")
    if evaluation["rubric_scores"]:
        for score in evaluation["rubric_scores"]:
            with st.container(border=True):
                st.markdown(f'**{score["criterion"]}** — úroveň **{score["level"]}/4**')
                st.caption(score["reason"])
    else:
        st.caption("Úrovne rubriky sa nepodarilo určiť.")

    st.subheader("Odporúčané úpravy")
    groups = (
        ("1. Nevyhnutné na odstránenie nesúladu", evaluation["required_changes"]),
        ("2. Dôležité na lepšie naplnenie kurikula", evaluation["important_changes"]),
        ("3. Voliteľné metodické zlepšenia", evaluation["optional_changes"]),
    )
    for heading, items in groups:
        st.markdown(f"**{heading}**")
        if items:
            for item in items:
                st.markdown(f"- {item}")
        else:
            st.caption("Bez zistení v tejto kategórii.")

    st.subheader("Záver pre učiteľa")
    st.markdown(f'**Čo určite zachovať:** {evaluation["keep"]}')
    st.markdown(f'**Čo treba upraviť:** {evaluation["must_change"]}')
    st.markdown(f'**Najdôležitejší ďalší krok:** {evaluation["next_step"]}')


init_state()
svp_rules, rubric, consultation_system_prompt = load_evaluation_sources()

st.title("Hodnotenie prípravy na vyučovaciu hodinu", anchor=False)
st.caption("Spätná väzba podľa slovenského Štátneho vzdelávacieho programu a projektovej rubriky")
if has_ai_credentials():
    st.badge("AI hodnotenie je pripojené (gpt-4.1-mini)", color="green", icon=":material/online_prediction:")
else:
    st.badge("Lokálne orientačné hodnotenie", color="orange", icon=":material/offline_bolt:")
    st.caption("Bez pripojeného AI modelu aplikácia používa konzervatívnu lokálnu kontrolu a nepredstiera presný súlad tam, kde chýbajú dôkazy.")

left, right = st.columns([1, 1], gap="large", vertical_alignment="top")

with left:
    with st.container(border=True):
        st.subheader("Príprava na vyučovaciu hodinu")
        uploaded = st.file_uploader(
            "Nahrať prípravu",
            type=["pdf", "docx", "txt", "md"],
            help="Podporované formáty: PDF, Word, TXT a Markdown.",
        )
        if uploaded is not None:
            upload_key = f"{uploaded.name}:{uploaded.size}"
            if st.session_state.get("loaded_upload") != upload_key:
                try:
                    st.session_state.lesson_plan = extract_text(uploaded.name, uploaded.getvalue())
                    st.session_state.lesson_plan_editor = st.session_state.lesson_plan
                    st.session_state.loaded_upload = upload_key
                    st.session_state.evaluation = None
                    st.session_state.consultation = []
                    st.rerun()
                except Exception as exc:
                    st.error(f"Súbor sa nepodarilo načítať: {exc}")

        if st.session_state.evaluation:
            st.segmented_control(
                "Zobrazenie textu",
                ["Upraviť text", "Farebné vyhodnotenie"],
                key="plan_view",
                label_visibility="collapsed",
                width="stretch",
            )

        if st.session_state.plan_view == "Farebné vyhodnotenie" and st.session_state.evaluation:
            st.html(annotated_plan(st.session_state.lesson_plan, st.session_state.evaluation["highlights"]))
            st.caption("Farba označuje úroveň hodnotenia; po ukázaní kurzorom sa zobrazí dôvod.")
        else:
            if "lesson_plan_editor" not in st.session_state:
                st.session_state.lesson_plan_editor = st.session_state.lesson_plan
            st.text_area(
                "Text prípravy",
                key="lesson_plan_editor",
                on_change=sync_lesson_plan_from_editor,
                height=610,
                placeholder="Sem vložte celú prípravu na vyučovaciu hodinu…",
            )
            st.caption(f'{len(st.session_state.lesson_plan)} znakov')

        if st.session_state.evaluation and st.session_state.lesson_plan != st.session_state.last_evaluated_plan:
            st.warning("Text sa od posledného hodnotenia zmenil. Spustite hodnotenie znova.", icon=":material/warning:")

        if st.button(
            "Vyhodnotiť prípravu",
            key="evaluate",
            type="primary",
            icon=":material/rate_review:",
            disabled=not st.session_state.lesson_plan.strip(),
            width="stretch",
        ):
            try:
                with st.spinner("Porovnávam prípravu so ŠVP a rubrikou…"):
                    st.session_state.evaluation = evaluate_lesson_plan(st.session_state.lesson_plan, svp_rules, rubric)
                    st.session_state.last_evaluated_plan = st.session_state.lesson_plan
                    st.session_state.consultation = []
                    st.session_state.plan_view = "Farebné vyhodnotenie"
                st.rerun()
            except Exception as exc:
                st.error(f"Hodnotenie sa nepodarilo dokončiť: {exc}")

question = None
with right:
    with st.container(border=True, height=860):
        st.subheader("Výsledok a konzultácia")
        if st.session_state.evaluation:
            render_feedback(st.session_state.evaluation)
            st.divider()
            st.subheader("Konzultácia k hodnoteniu")
            st.caption("Pýtajte sa na konkrétnu časť prípravy, zistenie alebo navrhovanú úpravu.")
            for message in st.session_state.consultation:
                avatar = ":material/person:" if message["role"] == "user" else ":material/school:"
                with st.chat_message(message["role"], avatar=avatar):
                    st.markdown(message["content"])
        else:
            st.info(
                "Vložte alebo nahrajte prípravu v ľavej časti a kliknite na „Vyhodnotiť prípravu“. "
                "Tu sa zobrazí celkový verdikt, konkrétne zistenia a konzultácia.",
                icon=":material/info:",
            )
        question = st.chat_input(
            "Opýtajte sa na konkrétnu časť prípravy…",
            key="consultation_input",
            disabled=not bool(st.session_state.evaluation),
        )
    if question and st.session_state.evaluation:
        st.session_state.consultation.append({"role": "user", "content": question})
        try:
            with st.spinner("Pripravujem odpoveď podľa ŠVP a rubriky…"):
                answer = consult_lesson_plan(
                    question, st.session_state.lesson_plan, st.session_state.evaluation,
                    svp_rules, rubric, consultation_system_prompt, st.session_state.consultation[:-1],
                )
            st.session_state.consultation.append({"role": "assistant", "content": answer})
            st.rerun()
        except Exception as exc:
            st.error(f"Odpoveď sa nepodarilo pripraviť: {exc}")
