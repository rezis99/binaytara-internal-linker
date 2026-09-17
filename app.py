"""Binaytara Internal Link Recommender: Streamlit front end."""
from __future__ import annotations

import streamlit as st

from config import settings
from engine import input_parser, retrieval, suggest
from output import excel_writer

st.set_page_config(page_title="Binaytara Internal Link Recommender",
                   page_icon="🔗", layout="wide")


@st.cache_resource(show_spinner="Loading the page database...")
def get_store():
    return retrieval.load()


def relevance_emoji(band: str) -> str:
    return {"High": "🟢", "Medium": "🟡", "Lower": "⚪"}.get(band, "")


def give_table(rows):
    return [{
        "Relevance": f"{relevance_emoji(r['relevance'])} {r['relevance']}",
        "Existing Sentence": r["existing_sentence"],
        "Modified Sentence": r["modified_sentence"],
        "Anchor Text": r["anchor"],
        "Target Page Link": r["target_url"],
        "Target Page Title": r["target_title"],
        "Section": r["section"],
        "Match Type": r["match_type"],
        "Topic Overlap (heuristic)": (
            r["overlap_level"] + (f": {r['overlap_why']}" if r["overlap_why"] else "")),
        "Notes": r["notes"],
    } for r in rows]


def receive_table(rows):
    return [{
        "Relevance": f"{relevance_emoji(r['relevance'])} {r['relevance']}",
        "Source Page": r["source_url"],
        "Source Page Title": r["source_title"],
        "Section": r["section"],
        "Existing Sentence": r["existing_sentence"],
        "Modified Sentence": r["modified_sentence"],
        "Anchor Text": r["anchor"],
        "Match Type": r["match_type"],
        "Topic Overlap (heuristic)": (
            r["overlap_level"] + (f": {r['overlap_why']}" if r["overlap_why"] else "")),
        "Notes": r["notes"],
    } for r in rows]


def render(res):
    a = res["article"]
    c = st.columns(5)
    c[0].metric("Word count", f"{a['word_count']:,}")
    c[1].metric("SOP benchmark", a["benchmark"])
    c[2].metric("Links to give", len(res["give"]))
    c[3].metric("Links to receive", len(res["receive"]))
    c[4].metric("Existing body links", len(a["existing_links"]))

    t1, t2, t3 = st.tabs([
        f"Links to Give ({len(res['give'])})",
        f"Links to Receive ({len(res['receive'])})",
        f"Existing links ({len(a['existing_links'])})",
    ])
    with t1:
        st.caption("Where in this article to place links out to existing pages. "
                   "The first paragraph, Key Takeaways, references and direct "
                   "quotes are excluded per the Content Publishing SOP.")
        if res["give"]:
            st.dataframe(give_table(res["give"]), use_container_width=True,
                         hide_index=True)
        else:
            st.info("No suggestions cleared the relevance threshold.")
    with t2:
        st.caption("Existing pages that should add a link pointing to this article.")
        if res["receive"]:
            st.dataframe(receive_table(res["receive"]), use_container_width=True,
                         hide_index=True)
        else:
            st.info("No suggestions cleared the relevance threshold.")
    with t3:
        if a["existing_links"]:
            st.write("These are already linked from the article body, so they are "
                     "never suggested again:")
            for link in a["existing_links"]:
                st.write(f"- {link}")
        else:
            st.write("No internal links found in the article body.")


def main():
    st.title("🔗 Binaytara Internal Link Recommender")

    try:
        store = get_store()
    except FileNotFoundError:
        st.error("No page database found. Run `python -m indexer.build_index` first.")
        st.stop()
    except RuntimeError as exc:
        st.error(str(exc))
        st.stop()

    age = retrieval.index_age_days(store)

    with st.sidebar:
        st.header("Options")
        mode = st.radio("Mode", ["Single article", "Batch"], horizontal=True)
        chosen = st.multiselect("Suggest links from these sections",
                                suggest.SECTIONS, default=suggest.SECTIONS)
        show_lower = st.checkbox("Show lower relevance suggestions", value=False)
        deorphan = False
        if mode == "Batch":
            deorphan = st.checkbox(
                "De-orphaning mode", value=False,
                help="Boosts pages that currently have few inbound internal links.")
        st.divider()
        st.caption(f"Pages indexed: {store.manifest.get('pages', 0):,}")
        st.caption(f"Database built: {store.manifest.get('built_at', '')[:10]}")
        if age > settings.INDEX_STALE_DAYS:
            st.warning(f"The page database is {age:.0f} days old. "
                       "Recent articles may be missing.")

    sections = set(chosen) if chosen else None

    if mode == "Single article":
        source = st.radio("Input", ["Paste a binaytara.org URL", "Upload a .docx draft"],
                          horizontal=True)
        article = None
        if source.startswith("Paste"):
            url = st.text_input("Article URL",
                                placeholder="https://binaytara.org/cancernews/article/...")
            if st.button("Analyse", type="primary", disabled=not url):
                with st.spinner("Reading the article and searching the site..."):
                    try:
                        article = input_parser.from_url(url)
                    except input_parser.InputError as exc:
                        st.error(str(exc))
        else:
            st.caption("Drafts are processed temporarily and never stored. Do not "
                       "upload patient-identifiable information or confidential or "
                       "embargoed manuscripts; this tool is publicly accessible.")
            up = st.file_uploader("Word document", type=["docx"])
            if up and st.button("Analyse", type="primary"):
                with st.spinner("Reading the draft and searching the site..."):
                    try:
                        article = input_parser.from_docx(up.getvalue(), up.name)
                    except input_parser.InputError as exc:
                        st.error(str(exc))

        if article is not None:
            with st.spinner("Scoring suggestions..."):
                res = suggest.analyse(article, store, sections, show_lower, deorphan)
            st.session_state["results"] = [res]

    else:
        st.caption(f"One binaytara.org URL per line, up to {settings.BATCH_MAX}.")
        raw = st.text_area("URLs", height=180)
        urls = [u.strip() for u in raw.splitlines() if u.strip()][: settings.BATCH_MAX]
        if st.button("Analyse batch", type="primary", disabled=not urls):
            out, bar = [], st.progress(0.0, text="Starting...")
            for i, u in enumerate(urls, 1):
                bar.progress(i / len(urls), text=f"{i}/{len(urls)}: {u}")
                try:
                    art = input_parser.from_url(u)
                    out.append(suggest.analyse(art, store, sections, show_lower, deorphan))
                except input_parser.InputError as exc:
                    st.warning(f"{u}: {exc}")
            bar.empty()
            st.session_state["results"] = out

    results = st.session_state.get("results") or []
    if results:
        st.divider()
        if len(results) == 1:
            render(results[0])
        else:
            for res in results:
                with st.expander(f"{res['article']['title']}  "
                                 f"({len(res['give'])} give / {len(res['receive'])} receive)"):
                    render(res)
        st.download_button(
            "⬇ Download Excel",
            data=excel_writer.build_workbook(results),
            file_name="binaytara-internal-link-suggestions.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )


if __name__ == "__main__":
    main()
