"""Hodnotenie príprav podľa slovenského ŠVP a projektovej rubriky."""

from __future__ import annotations

import io
import json
import os
import re
from typing import Any


LEVEL_META = {
    "nesulad": {"label": "NESÚLAD", "icon": "🔴", "color": "#b42318"},
    "nepreukazane": {"label": "NEDOSTATOČNE PREUKÁZANÉ", "icon": "🟠", "color": "#c2410c"},
    "odporucanie": {"label": "ODPORÚČANIE", "icon": "🟡", "color": "#a16207"},
    "sulad": {"label": "SÚLAD", "icon": "🟢", "color": "#15803d"},
}


def extract_text(name: str, data: bytes) -> str:
    """Načíta text z podporovaného súboru."""
    suffix = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if suffix in {"txt", "md"}:
        return data.decode("utf-8", errors="replace")
    if suffix == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == "docx":
        from docx import Document

        document = Document(io.BytesIO(data))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    raise ValueError("Podporované súbory sú PDF, DOCX, TXT a Markdown.")


def _client_and_model():
    from openai import OpenAI

    if os.getenv("OPENAI_API_KEY", "").strip():
        return OpenAI(), os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    return None, None


def has_ai_credentials() -> bool:
    return bool(
        os.getenv("OPENAI_API_KEY", "").strip()
    )


def _chat(system: str, messages: list[dict[str, str]]) -> str:
    client, model = _client_and_model()
    if client is None:
        raise RuntimeError("Nie je nastavené pripojenie k AI modelu.")
    response = client.chat.completions.create(
        model=model,
        temperature=0.15,
        messages=[{"role": "system", "content": system}, *messages],
    )
    return response.choices[0].message.content or ""


def _json_from_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _normalise_evaluation(data: dict[str, Any]) -> dict[str, Any]:
    valid_levels = set(LEVEL_META)
    highlights = []
    for item in data.get("highlights", []):
        if not isinstance(item, dict):
            continue
        level = str(item.get("level", "odporucanie")).lower()
        if level not in valid_levels:
            level = "odporucanie"
        quote = str(item.get("quote", "")).strip()
        if not quote:
            continue
        highlights.append(
            {
                "level": level,
                "quote": quote,
                "reason": str(item.get("reason", "")).strip(),
                "suggestion": str(item.get("suggestion", "")).strip(),
            }
        )
    chain = data.get("alignment_chain", {})
    rubric_scores = []
    for item in data.get("rubric_scores", []):
        if not isinstance(item, dict):
            continue
        try:
            level = min(4, max(1, int(item.get("level", 1))))
        except (TypeError, ValueError):
            level = 1
        rubric_scores.append(
            {
                "criterion": str(item.get("criterion", "Kritérium")),
                "level": level,
                "reason": str(item.get("reason", "")),
            }
        )
    return {
        "verdict": str(data.get("verdict", "Nemožno spoľahlivo posúdiť pre chýbajúce informácie")),
        "rationale": str(data.get("rationale", "")),
        "highlights": highlights,
        "strengths": [str(item) for item in data.get("strengths", [])][:5],
        "alignment_chain": {
            "Vzdelávací štandard": str(chain.get("Vzdelávací štandard", "nemožno overiť")),
            "Cieľ": str(chain.get("Cieľ", "nemožno overiť")),
            "Aktivita žiaka": str(chain.get("Aktivita žiaka", "nemožno overiť")),
            "Dôkaz o učení": str(chain.get("Dôkaz o učení", "nemožno overiť")),
            "Spätná väzba": str(chain.get("Spätná väzba", "nemožno overiť")),
        },
        "rubric_scores": rubric_scores,
        "required_changes": [str(item) for item in data.get("required_changes", [])],
        "important_changes": [str(item) for item in data.get("important_changes", [])],
        "optional_changes": [str(item) for item in data.get("optional_changes", [])],
        "keep": str(data.get("keep", "")),
        "must_change": str(data.get("must_change", "")),
        "next_step": str(data.get("next_step", "")),
    }


def evaluate_lesson_plan(plan: str, svp_rules: str, rubric: str) -> dict[str, Any]:
    """Vytvorí štruktúrované hodnotenie; bez API použije opatrnú lokálnu analýzu."""
    if not has_ai_credentials():
        return local_evaluation(plan)

    schema = """
Vráť IBA platný JSON bez markdownového bloku v tejto schéme:
{
  "verdict": "jedna z piatich povolených možností zo zadania",
  "rationale": "2 až 4 vety",
  "highlights": [
    {"level":"nesulad|nepreukazane|odporucanie|sulad", "quote":"presná doslovná citácia z plánu", "reason":"prečo", "suggestion":"najmenšia konkrétna úprava"}
  ],
  "strengths": ["najviac päť konkrétnych silných stránok"],
  "alignment_chain": {
    "Vzdelávací štandard":"jasný|čiastočný|chýba|nemožno overiť",
    "Cieľ":"jasný|čiastočný|chýba|nemožno overiť",
    "Aktivita žiaka":"jasný|čiastočný|chýba|nemožno overiť",
    "Dôkaz o učení":"jasný|čiastočný|chýba|nemožno overiť",
    "Spätná väzba":"jasný|čiastočný|chýba|nemožno overiť"
  },
  "rubric_scores": [
    {"criterion":"slovenský názov každého zo 6 kritérií rubriky", "level":1, "reason":"stručný dôkaz pre úroveň 1 až 4"}
  ],
  "required_changes":["nevyhnutné úpravy"],
  "important_changes":["dôležité úpravy"],
  "optional_changes":["voliteľné zlepšenia"],
  "keep":"čo zachovať",
  "must_change":"čo musí učiteľ upraviť",
  "next_step":"najdôležitejší ďalší krok"
}
Každá položka highlights musí obsahovať presnú súvislú citáciu zo vstupného plánu. Nevymýšľaj text, ktorý v pláne nie je.
V rubric_scores ohodnoť presne všetkých šesť kritérií z rubriky úrovňou 1 až 4 a názvy prelož do slovenčiny.
"""
    system = (
        "Odpovedáš výlučne po slovensky. Posudzuj dôkazy v pláne, nie osobu učiteľa. "
        "Prísne rozlišuj nesúlad, nedostatočne preukázaný súlad a nepovinné odporúčanie.\n\n"
        f"PRAVIDLÁ SLOVENSKÉHO ŠVP:\n{svp_rules}\n\n"
        f"DOPLNKOVÁ RUBRIKA ÚROVNÍ:\n{rubric}\n\n{schema}"
    )
    raw = _chat(system, [{"role": "user", "content": f"PRÍPRAVA NA VYUČOVACIU HODINU:\n\n{plan}"}])
    return _normalise_evaluation(_json_from_response(raw))


def local_evaluation(plan: str) -> dict[str, Any]:
    """Konzervatívne lokálne hodnotenie založené na znakoch v texte."""
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n|\n", plan) if part.strip()]

    def first_matching(terms: tuple[str, ...]) -> str:
        return next((p for p in paragraphs if any(term in p.lower() for term in terms)), "")

    checks = {
        "Vzdelávací štandard": first_matching(("výkonový štandard", "obsahový štandard", "štandard")),
        "Cieľ": first_matching(("cieľ", "žiak dokáže", "žiaci dokážu", "žiak vie", "žiaci vedia")),
        "Aktivita žiaka": first_matching(("úloha", "aktivita", "diskusia", "rieši", "vytvor", "porovná", "vysvetlia", "zdôvodnia", "pracujú")),
        "Dôkaz o učení": first_matching(("kritéri", "dôkaz", "over", "výstup", "exit", "produkt")),
        "Spätná väzba": first_matching(("spätná väzba", "formatív", "ďalší krok", "sebahodnot")),
    }
    highlights: list[dict[str, str]] = []
    for name, quote in checks.items():
        if quote:
            highlights.append(
                {
                    "level": "sulad",
                    "quote": quote,
                    "reason": f"Plán obsahuje konkrétny doklad pre oblasť: {name.lower()}.",
                    "suggestion": "Zachovajte túto časť a podľa potreby ju spresnite.",
                }
            )

    passive = first_matching(("učiteľ vysvetlí", "učiteľ odprednáša", "prebrať učivo", "oboznámiť žiakov"))
    has_active = bool(checks["Aktivita žiaka"])
    if passive and not has_active:
        highlights.append(
            {
                "level": "nesulad",
                "quote": passive,
                "reason": "Žiaci sú opísaní iba ako prijímatelia informácií bez možnosti preukázať porozumenie alebo poznatok použiť.",
                "suggestion": "Doplňte krátku žiacku úlohu, v ktorej žiaci vysvetlia, použijú alebo zdôvodnia nové poznanie.",
            }
        )
    elif passive:
        highlights.append(
            {
                "level": "odporucanie",
                "quote": passive,
                "reason": "Výklad nie je sám osebe v rozpore so ŠVP; treba však strážiť jeho väzbu na aktívne učenie.",
                "suggestion": "Po výklade jasne uveďte, ako žiaci nové poznanie spracujú alebo použijú.",
            }
        )

    missing = [name for name, quote in checks.items() if not quote]
    chain = {name: ("jasný" if quote else "nemožno overiť") for name, quote in checks.items()}
    required = []
    important = [f"Doplniť informáciu pre oblasť „{name}“ tak, aby sa jej splnenie dalo overiť." for name in missing]
    if passive and not has_active:
        required.append("Doplniť príležitosť, v ktorej žiaci aktívne preukážu porozumenie alebo použitie učiva.")
    evidence_count = sum(bool(value) for value in checks.values())
    rubric_scores = [
        {
            "criterion": "Ciele a zosúladenie cieľov, aktivít a hodnotenia",
            "level": 3 if checks["Cieľ"] and checks["Aktivita žiaka"] and checks["Dôkaz o učení"] else 2 if checks["Cieľ"] else 1,
            "reason": "Úroveň vychádza z doloženej väzby medzi cieľom, činnosťou žiaka a dôkazom o učení.",
        },
        {
            "criterion": "Dizajn založený na poznaní žiakov",
            "level": 2 if any(term in plan.lower() for term in ("vstupn", "predchádz", "potreb", "tried")) else 1,
            "reason": "Kontrola hľadá konkrétne informácie o východiskách a potrebách triedy.",
        },
        {
            "criterion": "Aktívne a hĺbkové učenie žiakov",
            "level": 3 if checks["Aktivita žiaka"] else 1,
            "reason": "Úroveň vychádza z opisu vysvetľovania, riešenia, tvorby alebo zdôvodňovania žiakmi.",
        },
        {
            "criterion": "Podpora učenia a riadenie kognitívnej záťaže",
            "level": 2 if any(term in plan.lower() for term in ("pomoc", "podpor", "návod", "príklad", "krok")) else 1,
            "reason": "Kontrola hľadá konkrétne opory pri náročných miestach učenia.",
        },
        {
            "criterion": "Formatívne hodnotenie a spätná väzba",
            "level": 3 if checks["Spätná väzba"] and checks["Dôkaz o učení"] else 2 if checks["Dôkaz o učení"] else 1,
            "reason": "Úroveň vychádza z doloženého zisťovania dôkazov a nadväzujúceho ďalšieho kroku.",
        },
        {
            "criterion": "Prístupnosť a zapojenie všetkých žiakov",
            "level": 2 if any(term in plan.lower() for term in ("diferenc", "podpor", "rozšíren", "špeciáln", "tempo")) else 1,
            "reason": "Kontrola hľadá konkrétne možnosti podpory, rozšírenia alebo alternatívneho zapojenia.",
        },
    ]
    verdict = (
        "V zásade v súlade, potrebné sú menšie úpravy"
        if evidence_count >= 4 and not required
        else "Čiastočne v súlade, potrebné sú podstatné úpravy"
        if evidence_count >= 2 or required
        else "Nemožno spoľahlivo posúdiť pre chýbajúce informácie"
    )
    return _normalise_evaluation(
        {
            "verdict": verdict,
            "rationale": (
                "Lokálna kontrola identifikovala iba prvky, ktoré sú priamo doložené v texte. "
                "Presný súlad so vzdelávacími štandardmi nemožno potvrdiť bez ich uvedenia; oranžové zistenia preto nepredstavujú automaticky nesúlad."
            ),
            "highlights": highlights,
            "strengths": [f"V texte je doložená oblasť: {name.lower()}." for name, quote in checks.items() if quote][:5],
            "alignment_chain": chain,
            "rubric_scores": rubric_scores,
            "required_changes": required,
            "important_changes": important,
            "optional_changes": ["Zvážiť primeraný priestor na reflexiu stratégie učenia alebo sebahodnotenie."],
            "keep": "Zachovať konkrétne formulácie, ktoré opisujú učenie a činnosť žiaka.",
            "must_change": required[0] if required else "Doplniť iba tie chýbajúce informácie, ktoré sú potrebné na overenie deklarovaného cieľa.",
            "next_step": important[0] if important else "Skontrolovať zrozumiteľnosť kritérií úspechu pre žiakov.",
        }
    )


def consult_lesson_plan(
    question: str,
    plan: str,
    evaluation: dict[str, Any],
    svp_rules: str,
    rubric: str,
    system_prompt: str,
    history: list[dict[str, str]],
) -> str:
    """Odpovie na následnú otázku učiteľa s kontextom plánu a hodnotenia."""
    if has_ai_credentials():
        system = build_consultation_system_prompt(system_prompt, svp_rules, rubric, plan, evaluation)
        return _chat(system, history[-10:] + [{"role": "user", "content": question}])

    query_words = {word for word in re.findall(r"\w+", question.lower()) if len(word) > 4}
    source_chunks = re.split(r"\n\s*\n", svp_rules)
    ranked = sorted(
        source_chunks,
        key=lambda chunk: sum(chunk.lower().count(word) for word in query_words),
        reverse=True,
    )
    excerpt = next((" ".join(chunk.split()) for chunk in ranked if any(word in chunk.lower() for word in query_words)), "")
    if excerpt:
        return (
            "Podľa pravidiel slovenského ŠVP je pri tejto otázke podstatné:\n\n"
            f"> {excerpt[:900]}\n\n"
            "V príprave preto odporúčam upraviť iba tú časť, ktorá je potrebná na preukázanie tejto väzby. "
            "Pre detailnú odpoveď ku konkrétnej formulácii pripojte AI model v nastavení prostredia."
        )
    return (
        "V lokálnom režime viem odpoveď oprieť iba o výslovne uvedené pravidlá a zistenia. "
        "Spresnite otázku pomenovaním časti plánu, napríklad cieľ, aktivita žiaka, hodnotenie, diferenciácia alebo spätná väzba."
    )


def build_consultation_system_prompt(
    system_prompt: str,
    svp_rules: str,
    rubric: str,
    plan: str,
    evaluation: dict[str, Any],
) -> str:
    """Zostaví systémový prompt následnej konzultácie z konfiguračných podkladov."""
    return (
        f"{system_prompt}\n\n"
        "# Záväzné pokyny pre túto konzultáciu\n"
        "- Odpovedaj výlučne po slovensky bez ohľadu na jazyk otázky.\n"
        "- Uplatni všetky pravidlá zo systémových inštrukcií vyššie.\n"
        "- Rozlišuj povinnosť ŠVP od metodického odporúčania.\n"
        "- Opieraj sa o presné dôkazy z aktuálnej prípravy a výsledku hodnotenia.\n"
        "- Ak cituješ prípravu, cituj ju doslovne.\n\n"
        f"# Pravidlá slovenského ŠVP\n{svp_rules}\n\n"
        f"# Hodnotiaca rubrika\n{rubric}\n\n"
        f"# Aktuálna príprava\n{plan}\n\n"
        f"# Výsledok hodnotenia\n{json.dumps(evaluation, ensure_ascii=False)}"
    )
