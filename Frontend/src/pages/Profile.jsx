// src/pages/Profile.jsx

import { useState } from "react";
import {
  ArrowLeft,
  User,
  Mail,
  Phone,
  Globe,
  Shield,
  Edit3,
  KeyRound,
  Download,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { API } from "../config/constants";
import { getAccessToken } from "../services/apiClient";

export default function Profile() {
  const navigate = useNavigate();
  const { user, isAuthenticated } = useAuth();
  const [downloading, setDownloading] = useState(false);

  if (!isAuthenticated) {
    return (
      <div className="px-4 py-6 text-center max-w-2xl">
        <p className="theme-text">Please sign in to view your profile.</p>
      </div>
    );
  }

  const displayName =
    user?.display_name || user?.full_name || user?.username || "N/A";
  const email = user?.email || "N/A";
  const phone = user?.phone_number || "Not provided";
  const language =
    user?.preferred_language === "sw"
      ? "Swahili"
      : user?.preferred_language === "sh"
        ? "Sheng"
        : "English";
  const initials = (displayName || "U").slice(0, 2).toUpperCase();

  const handleDownloadData = async () => {
    setDownloading(true);
    try {
      const token = getAccessToken();
      const response = await fetch(`${API.BASE_URL}${API.AUTH.EXPORT_DATA}`, {
        method: "GET",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/json",
        },
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error("Failed to download data");
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "uwenzai_data.json";
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      a.remove();
    } catch (err) {
      console.error("Download failed:", err);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="px-4 py-6 sm:px-6 lg:px-8 space-y-6 max-w-2xl">
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-2 text-sm font-medium theme-text-muted hover:underline"
      >
        <ArrowLeft size={16} />
        Back
      </button>

      <div className="text-center">
        <div
          className="mx-auto flex h-20 w-20 items-center justify-center rounded-full text-xl font-bold"
          style={{
            backgroundColor: "var(--color-accent-soft)",
            color: "var(--color-accent)",
          }}
        >
          {initials}
        </div>
        <h2 className="mt-4 text-2xl font-semibold theme-text">
          {displayName}
        </h2>
        <p className="mt-1 text-sm theme-text-muted">Your profile details</p>
      </div>

      <div className="rounded-2xl border p-4 space-y-3 theme-card">
        <DetailRow
          icon={User}
          label="Username"
          value={user?.username || "N/A"}
        />
        <DetailRow icon={Mail} label="Email" value={email} />
        <DetailRow icon={Phone} label="Phone" value={phone} />
        <DetailRow icon={Globe} label="Language" value={language} />
        <DetailRow
          icon={Shield}
          label="Account Status"
          value={formatAccountStatus(user?.account_status)}
        />
      </div>

      <div className="space-y-3">
        <button
          onClick={() => navigate("/edit-profile")}
          className="flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-medium transition hover:opacity-90"
          style={{
            backgroundColor: "var(--color-accent-soft)",
            color: "var(--color-accent)",
            border: "1px solid var(--color-accent-soft)",
          }}
        >
          <Edit3 size={18} />
          Edit Profile
        </button>

        <button
          onClick={handleDownloadData}
          disabled={downloading}
          className="flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
          style={{
            backgroundColor: "var(--color-soft)",
            color: "var(--color-text)",
            border: "1px solid var(--color-border)",
          }}
        >
          <Download size={18} />
          {downloading ? "Downloading…" : "Download My Data"}
        </button>

        <button
          onClick={() => navigate("/change-password")}
          className="flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-medium transition hover:opacity-90"
          style={{
            backgroundColor: "var(--color-soft)",
            color: "var(--color-text)",
            border: "1px solid var(--color-border)",
          }}
        >
          <KeyRound size={18} />
          Change Password
        </button>
      </div>
    </div>
  );
}

// Maps raw CustomUser.AccountStatus values to readable labels.
// Kept in sync with Backend/capp/models.py::CustomUser.AccountStatus.
const ACCOUNT_STATUS_LABELS = {
  active: "Active",
  pending: "Pending",
  suspended: "Suspended",
  banned: "Banned",
  deactivated: "Deactivated",
  safety_locked: "Locked (safety review)",
};

function formatAccountStatus(value) {
  if (!value) return "Active";
  return ACCOUNT_STATUS_LABELS[value] || value;
}

function DetailRow({ icon: Icon, label, value }) {
  return (
    <div className="flex items-center gap-3 py-2">
      <Icon size={18} className="text-[var(--color-accent)] shrink-0" />
      <div className="flex-1">
        <p className="text-xs font-medium theme-text-muted uppercase">
          {label}
        </p>
        <p className="text-sm theme-text">{value}</p>
      </div>
    </div>
  );
}
