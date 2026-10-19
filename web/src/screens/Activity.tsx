// Home live feed (owner request 2026-10-19: "show what the system is doing"): GET /api/activity, newest first, asked
// for every 15 s. Read-only. Colour by kind with the design-system tones: opened = info, closed = ok (won) or warn
// (lost), refused = idle. Red stays for what stops trading (docs/ux/design-system.md), so no event here is red.
import { useQuery } from "@tanstack/react-query";
import { ACTIVITY_LIMIT, ACTIVITY_POLL_MS, eventLine, label, SOURCE_WORDS, tone } from "../lib/activity";
import { api, type ActivityView } from "../lib/api";
import { Badge, Card, EmptyState, ErrorState, Skeleton, TimeText } from "../ui";

export function ActivityFeed() {
  const q = useQuery({ queryKey: ["activity"], queryFn: () => api.activity(ACTIVITY_LIMIT), refetchInterval: ACTIVITY_POLL_MS });
  const aside = q.dataUpdatedAt ? <>updated <TimeText iso={new Date(q.dataUpdatedAt).toISOString()} /></> : undefined;
  return (
    <Card title="Activity" aside={aside} label="Activity feed">
      {q.isLoading ? <Skeleton height={120} label="Loading activity" />
        : q.isError && !q.data ? <ErrorState title="Could not load activity." stillWorks="Trading is not affected." onRetry={() => q.refetch()} />
        : <Feed v={q.data!} />}
    </Card>
  );
}

function Feed({ v }: { v: ActivityView }) {
  return (
    <>
      {v.unreadable.length > 0 && (
        <p className="small state warn">{`Couldn't read: ${v.unreadable.map((s) => SOURCE_WORDS[s]).join(", ")}. The rest is shown.`}</p>
      )}
      {v.events.length === 0 ? (
        <EmptyState title="Nothing yet">Shadow trades, demo trades, cost probes and refused entries show here as they happen.</EmptyState>
      ) : (
        <ul className="activity" aria-label="Activity">
          {v.events.map((e, i) => (
            <li key={`${e.ts}-${e.kind}-${e.agent_id ?? ""}-${i}`} className={`activity-row ${tone(e)}`}>
              <span className="activity-time muted small"><TimeText iso={e.ts} withDay /></span>
              <Badge tone={tone(e)}>{label(e)}</Badge>
              <span className="activity-line num">{eventLine(e)}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
