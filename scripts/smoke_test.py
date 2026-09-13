"""Phase 0 definition of done: embed a document, store it, retrieve it both ways.

Inserts a synthetic case, embeds its paragraphs, then runs a vector query and a
lexical query against it. Re-runnable: clears its own rows first.
"""

from app.db import connect
from app.embeddings import embed, get_model

PARAS = [
    (1, "The appellant slipped on an unmarked wet floor in the respondent's grocery "
        "store and fractured her wrist."),
    (2, "The issue is whether a commercial occupier discharged its duty to take "
        "reasonable care to keep the premises safe for invitees."),
    (3, "An occupier is not an insurer against every injury; the standard is "
        "reasonableness in all the circumstances, not perfection."),
]

VECTOR_QUERY = "what does a store owe a customer who fell on a slippery floor"
LEXICAL_QUERY = "occupier invitee"


def main() -> None:
    model = get_model()
    print(f"model: {model.model_card_data.base_model or 'loaded'} on {model.device}")

    vectors = embed([text for _, text in PARAS])
    query_vector = embed([VECTOR_QUERY])[0]
    print(f"embedded {len(vectors)} paragraphs, dim={vectors.shape[1]}")

    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM cases WHERE source = 'smoke-test'")
        cur.execute(
            """
            INSERT INTO cases (neutral_citation, style_of_cause, court, jurisdiction,
                               decision_date, language, source, full_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            ("0000 TEST 1", "Smoke v. Test", "TEST", "federal", "2026-01-01", "en",
             "smoke-test", "\n".join(text for _, text in PARAS)),
        )
        case_id = cur.fetchone()[0]

        for (para_no, text), vector in zip(PARAS, vectors):
            cur.execute(
                "INSERT INTO case_chunks (case_id, para_no, text, embedding) "
                "VALUES (%s, %s, %s, %s)",
                (case_id, para_no, text, vector),
            )
        conn.commit()

        cur.execute(
            """
            SELECT para_no, 1 - (embedding <=> %s) AS similarity, text
            FROM case_chunks
            WHERE case_id = %s
            ORDER BY embedding <=> %s
            LIMIT 3
            """,
            (query_vector, case_id, query_vector),
        )
        vector_hits = cur.fetchall()

        cur.execute(
            """
            SELECT para_no, ts_rank(tsv, websearch_to_tsquery('english', %s)) AS rank, text
            FROM case_chunks
            WHERE case_id = %s AND tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY rank DESC
            """,
            (LEXICAL_QUERY, case_id, LEXICAL_QUERY),
        )
        lexical_hits = cur.fetchall()

    print(f"\nvector query: {VECTOR_QUERY!r}")
    for para_no, similarity, text in vector_hits:
        print(f"  para {para_no}  sim={similarity:.3f}  {text[:70]}...")

    print(f"\nlexical query: {LEXICAL_QUERY!r}")
    for para_no, rank, text in lexical_hits:
        print(f"  para {para_no}  rank={rank:.4f}  {text[:70]}...")

    assert vector_hits, "vector query returned nothing"
    assert lexical_hits, "lexical query returned nothing"
    print("\nPhase 0 DoD met: schema + embeddings + vector and lexical retrieval all work.")


if __name__ == "__main__":
    main()
