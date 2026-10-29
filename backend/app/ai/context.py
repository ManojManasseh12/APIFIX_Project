def _evidence_block(retrieval) -> str:
    evidence = []
    for chunk in retrieval.chunks:
        evidence.append(
            "BEGIN EVIDENCE [{cid}] source={title} section={section}\n{text}\nEND EVIDENCE [{cid}]".format(
                cid=chunk["chunk_id"],
                title=chunk.get("source_title", ""),
                section=chunk.get("section") or "",
                text=chunk.get("chunk_text", "")[:1200],
            )
        )
    return "\n\n".join(evidence) if evidence else "(no matching documentation retrieved)"


def build_context(spec, obs, retrieval) -> str:
    observed = {
        "method": spec.method,
        "url": str(spec.url),
        "status": obs.status,
        "error_type": obs.error_type,
        "latency_ms": obs.latency_ms,
        "response_headers": obs.headers,
        "response_body_preview": obs.body_preview[:1500],
        "redirect_location": obs.redirect_location,
    }
    return (
        f"USER QUESTION:\n{spec.question or 'Why did this request fail?'}\n\n"
        f"OBSERVED REQUEST/RESPONSE (redacted):\n{observed}\n\n"
        "EVIDENCE:\n" + _evidence_block(retrieval)
    )


def build_ask_context(question: str, history, retrieval) -> str:
    turns = []
    for message in list(history)[-4:]:
        role = getattr(message, "role", "user")
        content = getattr(message, "content", "")[:1500]
        turns.append(f"{role.upper()}: {content}")
    conversation = "\n".join(turns) if turns else "(new conversation)"

    return (
        f"CONVERSATION:\n{conversation}\n\n"
        f"CURRENT QUESTION:\n{question}\n\n"
        "EVIDENCE:\n" + _evidence_block(retrieval)
    )
