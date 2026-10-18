import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ReasonCode } from "../lib/api";

type Vars = { id: string; created: number; action: "approve" | "reject"; reason?: ReasonCode };
type Ref = { proposal_id: string; created: number };

/** Approve / reject one proposal; shared by Home (the waiting trade) and Trades. Errors are kept per proposal. */
export function useDecide() {
  const qc = useQueryClient();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const m = useMutation({
    mutationFn: (v: Vars) => api.decide(v.id, v.created, v.action, v.reason),
    onMutate: (v) => setErrors(({ [v.id]: _, ...rest }) => rest),
    onError: (e, v) => setErrors((x) => ({ ...x, [v.id]: (e as Error).message })),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["proposals"] });
      qc.invalidateQueries({ queryKey: ["proposals-recent"] });
    },
  });
  return {
    errors,
    busyId: m.isPending ? m.variables?.id : undefined,
    approve: (p: Ref) => m.mutate({ id: p.proposal_id, created: p.created, action: "approve" }),
    reject: (p: Ref, reason?: ReasonCode) => m.mutate({ id: p.proposal_id, created: p.created, action: "reject", reason }),
  };
}
