interface Props {
  phase: string;
  autoApproveReads: boolean;
  active: boolean;
  onToggleReads: (value: boolean) => void;
  onStop: () => void;
}

export default function RunControls({
  phase,
  autoApproveReads,
  active,
  onToggleReads,
  onStop,
}: Props) {
  return (
    <div className="panel">
      <div className="panel-body" style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <div>
          <label>Phase</label>
          <span className="badge open">{phase}</span>
        </div>
        <div style={{ flex: 1 }}>
          <label>
            <input
              type="checkbox"
              checked={autoApproveReads}
              onChange={(e) => onToggleReads(e.target.checked)}
              style={{ width: "auto", marginRight: 8 }}
            />
            Auto-approve safe reads
          </label>
        </div>
        <button className="danger" onClick={onStop} disabled={!active}>
          STOP
        </button>
      </div>
    </div>
  );
}
