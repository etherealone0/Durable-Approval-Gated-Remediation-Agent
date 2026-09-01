"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type PendingRun = {
  run_id: string;
  status: string;
  diagnosis: string | null;
  proposed_action: string | null;
  risk_tier: string | null;
  rationale: string | null;
};

type Decision = "approved" | "edited" | "rejected";

export default function ApprovalQueuePage() {
  const [runs, setRuns] = useState<PendingRun[]>([]);
  const [approverId, setApproverId] = useState("ui-operator");
  const [editedActionByRun, setEditedActionByRun] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busyRunId, setBusyRunId] = useState<string | null>(null);

  async function loadPending() {
    setError(null);
    try {
      const res = await fetch(`${API_URL}/runs/pending-approval`);
      if (!res.ok) throw new Error(`GET /runs/pending-approval failed: ${res.status}`);
      setRuns(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    loadPending();
  }, []);

  async function submitDecision(runId: string, decision: Decision) {
    setBusyRunId(runId);
    setError(null);
    try {
      const body: Record<string, string> = { decision, approver_id: approverId };
      if (decision === "edited") {
        const editedAction = editedActionByRun[runId]?.trim();
        if (!editedAction) {
          setError("Enter an edited action (tool:target) before submitting an edit.");
          return;
        }
        body.edited_action = editedAction;
      }
      const res = await fetch(`${API_URL}/runs/${runId}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`POST /runs/${runId}/decision failed: ${res.status}`);
      await loadPending();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyRunId(null);
    }
  }

  return (
    <main style={{ maxWidth: 760 }}>
      <h1>Pending approvals</h1>

      <label style={{ display: "block", marginBottom: "1rem" }}>
        Approver ID:{" "}
        <input value={approverId} onChange={(e) => setApproverId(e.target.value)} />
      </label>

      <button onClick={loadPending} style={{ marginBottom: "1rem" }}>
        Refresh
      </button>

      {error && <p style={{ color: "crimson" }}>{error}</p>}

      {runs.length === 0 && <p>No runs awaiting approval.</p>}

      {runs.map((run) => (
        <section
          key={run.run_id}
          style={{ border: "1px solid #ccc", borderRadius: 8, padding: "1rem", marginBottom: "1rem" }}
        >
          <h2 style={{ marginTop: 0 }}>{run.run_id}</h2>
          <p>
            <strong>Risk tier:</strong> {run.risk_tier ?? "—"}
          </p>
          <p>
            <strong>Diagnosis:</strong> {run.diagnosis ?? "—"}
          </p>
          <p>
            <strong>Proposed action:</strong> {run.proposed_action ?? "—"}
          </p>
          <p>
            <strong>Rationale:</strong> {run.rationale ?? "—"}
          </p>

          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
            <button disabled={busyRunId === run.run_id} onClick={() => submitDecision(run.run_id, "approved")}>
              Approve
            </button>
            <button disabled={busyRunId === run.run_id} onClick={() => submitDecision(run.run_id, "rejected")}>
              Reject
            </button>
            <input
              placeholder="tool:target"
              value={editedActionByRun[run.run_id] ?? ""}
              onChange={(e) =>
                setEditedActionByRun((prev) => ({ ...prev, [run.run_id]: e.target.value }))
              }
            />
            <button disabled={busyRunId === run.run_id} onClick={() => submitDecision(run.run_id, "edited")}>
              Submit edit
            </button>
          </div>
        </section>
      ))}
    </main>
  );
}
