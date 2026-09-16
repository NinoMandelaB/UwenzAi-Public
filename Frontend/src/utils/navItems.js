import {
  House,
  Users,
  MessageCircle,
  Sparkles as SparkPool,
  Settings,
  Code2,
} from "lucide-react";

export const NAV_ITEMS = (t) => [
  { to: "/dashboard", label: t("common.home"), icon: House },
  { to: "/characters", label: t("common.characters"), icon: Users },
  { to: "/chats", label: t("common.chats") || "Chats", icon: MessageCircle },
  { to: "/spark_pool", label: t("common.spark_pool"), icon: SparkPool },
  { to: "/settings", label: t("common.settings"), icon: Settings },
  {
    to: "/uwenzai-dev",
    label: t("uwenzaiDev.title"),
    shortLabel: "Dev",
    icon: Code2,
    badge: t("uwenzaiDev.experimental"),
  },
];

// For mobile bottom nav - filter out items with mobile: false.
// Items can define shortLabel to stay legible when six entries share the bar.
export const BOTTOM_NAV_ITEMS = (t) =>
  NAV_ITEMS(t).filter((item) => item.mobile !== false);
