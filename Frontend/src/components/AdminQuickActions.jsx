// src/components/AdminQuickActions.jsx

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Briefcase,
  Heart,
  MessageSquare,
  ShieldAlert,
  Sparkles,
  Flag,
  Repeat,
} from "lucide-react";

import SparkPackagesModal from "./Admin/SparkPackagesModal";
import SubscriptionPlansModal from "./Admin/SubscriptionPlansModal";

function ActionCard({ icon: Icon, label, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="
        group
        flex
        aspect-square
        w-full
        flex-col
        items-center
        justify-center
        gap-2
        rounded-2xl
        p-2
        shadow-sm
        transition-all
        duration-200
        hover:-translate-y-0.5
        hover:shadow-md
        active:scale-[0.98]
      "
      style={{
        backgroundColor: "var(--color-surface)",
        color: "var(--color-text)",
        border: "1px solid var(--color-border)",
      }}
    >
      <div
        className="
          flex
          h-10
          w-10
          items-center
          justify-center
          text-sky-400
          transition-transform
          duration-200
          group-hover:scale-105
        "
      >
        <Icon size={20} strokeWidth={2} />
      </div>

      <span className="text-[13px] font-medium leading-tight sm:text-sm text-center">
        {label}
      </span>
    </button>
  );
}

function SectionDivider({ text }) {
  return (
    <div className="col-span-full flex items-center gap-3 py-1">
      <div
        className="h-px flex-1"
        style={{ backgroundColor: "var(--color-border)" }}
      />
      <span
        className="shrink-0 text-[11px] font-medium uppercase tracking-wide"
        style={{ color: "var(--color-text-muted)" }}
      >
        {text}
      </span>
      <div
        className="h-px flex-1"
        style={{ backgroundColor: "var(--color-border)" }}
      />
    </div>
  );
}

export default function AdminQuickActions() {
  const navigate = useNavigate();

  // Modal state for monetization cards
  const [sparkModalOpen, setSparkModalOpen] = useState(false);
  const [subModalOpen, setSubModalOpen] = useState(false);

  const mainActions = [
    {
      icon: ShieldAlert,
      label: "Pending",
      path: "/characters/pending",
    },
    {
      icon: Briefcase,
      label: "Occupations",
      path: "/admin/occupations",
    },
    {
      icon: Heart,
      label: "Hobbies",
      path: "/admin/hobbies",
    },
    {
      icon: Sparkles,
      label: "Traits",
      path: "/admin/personalities",
    },
    {
      icon: MessageSquare,
      label: "Feedback",
      path: "/admin/feedback",
    },
    {
      icon: Flag,
      label: "Flagged Characters",
      path: "/admin/flagged-characters",
    },
    {
      icon: Flag,
      label: "Flagged Messages",
      path: "/admin/flagged-messages",
    },
  ];

  const monetizationActions = [
    {
      icon: Sparkles,
      label: "Spark Packages",
      onClick: () => setSparkModalOpen(true),
    },
    {
      icon: Repeat,
      label: "Subscription Plans",
      onClick: () => setSubModalOpen(true),
    },
  ];

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-5">
        {mainActions.map((action) => (
          <ActionCard
            key={action.label}
            icon={action.icon}
            label={action.label}
            onClick={() => navigate(action.path)}
          />
        ))}

        <SectionDivider text="Monetization" />

        {monetizationActions.map((action) => (
          <ActionCard
            key={action.label}
            icon={action.icon}
            label={action.label}
            onClick={action.onClick}
          />
        ))}
      </div>

      {/* Modals */}
      <SparkPackagesModal
        open={sparkModalOpen}
        onClose={() => setSparkModalOpen(false)}
      />
      <SubscriptionPlansModal
        open={subModalOpen}
        onClose={() => setSubModalOpen(false)}
      />
    </div>
  );
}
