function getStatusStyles(status) {
  if (status === "Online") {
    return {
      borderColor: "color-mix(in srgb, var(--color-success) 25%, transparent)",
      backgroundColor:
        "color-mix(in srgb, var(--color-success) 12%, transparent)",
      color: "var(--color-success)",
    };
  }

  if (status === "Busy") {
    return {
      borderColor: "color-mix(in srgb, var(--color-warning) 25%, transparent)",
      backgroundColor:
        "color-mix(in srgb, var(--color-warning) 12%, transparent)",
      color: "var(--color-warning)",
    };
  }

  if (status === "Popular") {
    return {
      borderColor: "var(--color-accent-soft)",
      backgroundColor: "var(--color-accent-soft)",
      color: "var(--color-accent)",
    };
  }

  if (status === "Guest") {
    return {
      borderColor: "color-mix(in srgb, var(--color-guest) 25%, transparent)",
      backgroundColor:
        "color-mix(in srgb, var(--color-guest) 12%, transparent)",
      color: "var(--color-guest)",
    };
  }

  return {
    borderColor: "var(--color-border)",
    backgroundColor: "var(--color-soft)",
    color: "var(--color-text-muted)",
  };
}

export default function StatusBadge({ status }) {
  return (
    <span
      className="rounded-full border px-2.5 py-1 text-[11px] font-medium"
      style={getStatusStyles(status)}
    >
      {status}
    </span>
  );
}
