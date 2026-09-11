// src/pages/Contact.jsx
import { Building2, MapPin, Mail, MessageSquareHeart } from "lucide-react";
import PageCard from "../components/PageCard";
import SectionTitle from "../components/SectionTitle";
import FeedbackForm from "../components/FeedbackForm";
import { useLanguage } from "../context/LanguageContext";

function InstagramIcon({ size = 18, className = "" }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <rect width="20" height="20" x="2" y="2" rx="5" ry="5" />
      <path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z" />
      <line x1="17.5" x2="17.51" y1="6.5" y2="6.5" />
    </svg>
  );
}

function TikTokIcon({ size = 18, className = "" }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      <path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64 2.93 2.93 0 0 1 .88.13V9.4a6.84 6.84 0 0 0-1-.05A6.33 6.33 0 0 0 5 20.1a6.34 6.34 0 0 0 10.86-4.43v-7a8.16 8.16 0 0 0 4.77 1.52v-3.4a4.85 4.85 0 0 1-1-.1z" />
    </svg>
  );
}

function ContactRow({ icon: Icon, label, children }) {
  return (
    <div className="flex items-start gap-3">
      <div
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border"
        style={{
          backgroundColor: "var(--color-soft)",
          borderColor: "var(--color-border)",
          color: "var(--color-accent)",
        }}
      >
        <Icon size={16} />
      </div>
      <div className="min-w-0 pt-1">
        <p className="text-xs font-semibold uppercase tracking-wide theme-text-muted">
          {label}
        </p>
        <div className="mt-0.5 text-sm theme-text">{children}</div>
      </div>
    </div>
  );
}

export default function Contact() {
  const { t } = useLanguage();

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-black theme-text">{t("contact.title")}</h1>
        <p className="mt-1 text-sm theme-text-muted">{t("contact.subtitle")}</p>
      </div>

      {/* ── Contact details ── */}
      <section className="space-y-3">
        <SectionTitle>{t("contact.detailsTitle")}</SectionTitle>
        <PageCard className="space-y-5">
          <ContactRow icon={Building2} label={t("contact.companyLabel")}>
            {t("contact.company")}
          </ContactRow>

          <ContactRow icon={MapPin} label={t("contact.addressLabel")}>
            {t("contact.address")}
          </ContactRow>

          <ContactRow icon={Mail} label={t("contact.emailLabel")}>
            <a
              href="mailto:info@uwenzai.com"
              className="underline text-[var(--color-accent)]"
            >
              info@uwenzai.com
            </a>
          </ContactRow>

          <div
            className="border-t pt-4"
            style={{ borderColor: "var(--color-border)" }}
          >
            <ContactRow
              icon={MessageSquareHeart}
              label={t("contact.socialLabel")}
            >
              <div className="flex flex-wrap gap-2">
                <a
                  href="https://www.tiktok.com/@uwenzai"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 rounded-xl border px-3 py-2 text-sm font-medium theme-text transition hover:opacity-90"
                  style={{ borderColor: "var(--color-border)" }}
                >
                  <TikTokIcon size={16} />
                  TikTok
                </a>
                <a
                  href="https://www.instagram.com/uwenzai"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 rounded-xl border px-3 py-2 text-sm font-medium theme-text transition hover:opacity-90"
                  style={{ borderColor: "var(--color-border)" }}
                >
                  <InstagramIcon size={16} />
                  Instagram
                </a>
              </div>
            </ContactRow>
          </div>
        </PageCard>
      </section>

      {/* ── Feedback form ── */}
      <section className="space-y-3">
        <SectionTitle>{t("contact.feedbackTitle")}</SectionTitle>
        <PageCard>
          <p className="mb-4 text-sm theme-text-muted">
            {t("contact.feedbackDescription")}
          </p>
          <FeedbackForm />
        </PageCard>
      </section>
    </div>
  );
}
