// src/components/SupportSection.jsx

import { useState } from "react";
import {
  Info,
  UserRound,
  ChevronRight,
  X,
  Mail,
  MapPin,
  MessageSquare,
} from "lucide-react";
import SectionTitle from "./SectionTitle";
import FeedbackButton from "./FeedbackButton";

function SettingsItem({ title, subtitle, icon: Icon, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full rounded-2xl border p-4 text-left transition hover:opacity-95 theme-card"
    >
      <div className="flex items-center gap-3">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border"
          style={{
            backgroundColor: "var(--color-soft)",
            borderColor: "var(--color-border)",
            color: "var(--color-accent)",
          }}
        >
          <Icon size={18} />
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold theme-text">{title}</p>
          <p className="mt-1 text-sm leading-6 theme-text-muted">{subtitle}</p>
        </div>

        <div className="shrink-0 theme-text-muted">
          <ChevronRight size={18} />
        </div>
      </div>
    </button>
  );
}

export default function SupportSection({ currentLanguage, currentTheme }) {
  const [showContact, setShowContact] = useState(false);
  const [showFeedback, setShowFeedback] = useState(false);

  return (
    <>
      <section className="space-y-3">
        <SectionTitle>Support</SectionTitle>

        <div className="space-y-3">
          <SettingsItem
            title="Help Center"
            subtitle="Contact info, and feedback"
            icon={Info}
            onClick={() => setShowContact(true)}
          />

          <SettingsItem
            title="About UwenzAi"
            subtitle={`Language: ${currentLanguage} • Theme: ${currentTheme}`}
            icon={UserRound}
            onClick={() => {}}
          />
        </div>
      </section>

      {/* Contact Modal */}
      {showContact && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-sm rounded-2xl p-6 shadow-lg theme-card">
            <div className="flex items-start justify-between mb-4">
              <h3 className="text-lg font-semibold theme-text">Help Center</h3>
              <button
                onClick={() => setShowContact(false)}
                className="rounded-full p-1 text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
              >
                <X size={20} />
              </button>
            </div>

            <div className="space-y-4 text-sm theme-text">
              <div className="flex items-center gap-3">
                <Mail
                  size={18}
                  className="text-[var(--color-accent)] shrink-0"
                />
                <span>
                  <a
                    href="mailto:info@uwenzai.com"
                    className="underline text-[var(--color-accent)]"
                  >
                    info@uwenzai.com
                  </a>
                </span>
              </div>

              <div className="flex items-center gap-3">
                <MapPin
                  size={18}
                  className="text-[var(--color-accent)] shrink-0"
                />
                <span>
                  UwenzAi Limited – Nairobi, Kenya
                  <br />
                  P.O. Box 13583 - 00800
                </span>
              </div>

              <button
                onClick={() => {
                  setShowContact(false);
                  setTimeout(() => setShowFeedback(true), 150);
                }}
                className="flex items-center justify-center gap-2 w-full rounded-xl px-4 py-3 text-sm font-medium bg-[var(--color-accent-soft)] text-[var(--color-accent)] hover:opacity-90 transition"
              >
                <MessageSquare size={18} />
                Send Feedback
              </button>
            </div>
          </div>
        </div>
      )}

      <FeedbackButton
        open={showFeedback}
        onClose={() => setShowFeedback(false)}
      />
    </>
  );
}
