import { X, CircleAlert, CircleCheck, Info, TriangleAlert } from "lucide-react";

import { useTheme } from "../context/ThemeContext";

const typeConfig = {
  success: {
    icon: CircleCheck,
    accent: "#22c55e",
    soft: "rgba(34,197,94,0.12)",
    border: "rgba(34,197,94,0.22)",
    title: "Success",
  },
  error: {
    icon: CircleAlert,
    accent: "#ef4444",
    soft: "rgba(239,68,68,0.12)",
    border: "rgba(239,68,68,0.22)",
    title: "Error",
  },
  warning: {
    icon: TriangleAlert,
    accent: "#f59e0b",
    soft: "rgba(245,158,11,0.12)",
    border: "rgba(245,158,11,0.22)",
    title: "Warning",
  },
  info: {
    icon: Info,
    accent: "#3b82f6",
    soft: "rgba(59,130,246,0.12)",
    border: "rgba(59,130,246,0.22)",
    title: "Information",
  },
};

export default function MessagePopup({
  open,
  message,
  type = "info",
  title,
  onClose,
}) {
  const { isDark } = useTheme();

  if (!open || !message) return null;

  const config = typeConfig[type] || typeConfig.info;
  const Icon = config.icon;

  return (
    <div className="fixed inset-0 z-[999] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 backdrop-blur-sm"
        style={{
          backgroundColor: isDark ? "rgba(0,0,0,0.5)" : "rgba(15,23,42,0.16)",
        }}
        onClick={onClose}
      />

      {/* Modal */}
      <div
        role="alertdialog"
        aria-modal="true"
        aria-live="assertive"
        className="
          relative
          w-full
          max-w-md
          rounded-2xl
          border
          shadow-2xl
          animate-in
          fade-in
          zoom-in-95
          duration-200
        "
        style={{
          background: isDark
            ? "rgba(15, 23, 42, 0.96)"
            : "rgba(255,255,255,0.96)",
          borderColor: isDark
            ? "rgba(255,255,255,0.08)"
            : "rgba(15,23,42,0.08)",
          backdropFilter: "blur(16px)",
          boxShadow: isDark
            ? "0 16px 40px rgba(0,0,0,0.4)"
            : "0 16px 40px rgba(15,23,42,0.12)",
        }}
      >
        <div className="p-4">
          <div className="flex items-start gap-3">
            {/* Icon */}
            <div
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl"
              style={{
                background: config.soft,
                border: `1px solid ${config.border}`,
              }}
            >
              <Icon
                size={22}
                strokeWidth={2.4}
                style={{
                  color: config.accent,
                }}
              />
            </div>

            {/* Content */}
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-2">
                <h2
                  className="text-xl font-semibold leading-tight"
                  style={{
                    color: isDark ? "#f8fafc" : "#0f172a",
                  }}
                >
                  {title || config.title}
                </h2>

                <button
                  onClick={onClose}
                  className="
                    flex
                    h-8
                    w-8
                    shrink-0
                    items-center
                    justify-center
                    rounded-full
                    transition-all
                    duration-200
                    hover:scale-105
                  "
                  style={{
                    background: isDark
                      ? "rgba(255,255,255,0.05)"
                      : "rgba(15,23,42,0.05)",
                    color: isDark ? "#94a3b8" : "#64748b",
                  }}
                >
                  <X size={17} />
                </button>
              </div>

              <p
                className="mt-1.5 text-[15px] leading-6"
                style={{
                  color: isDark ? "#cbd5e1" : "#475569",
                }}
              >
                {message}
              </p>

              <div className="mt-4 flex justify-end">
                <button
                  onClick={onClose}
                  className="
                    rounded-xl
                    px-4
                    py-2
                    text-sm
                    font-medium
                    transition-all
                    duration-200
                    hover:scale-[1.02]
                  "
                  style={{
                    background: config.accent,
                    color: "#fff",
                  }}
                >
                  Okay
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
