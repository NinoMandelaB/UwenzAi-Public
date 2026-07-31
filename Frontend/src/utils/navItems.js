import {
  House,
  Users,
  MessageCircle,
  Sparkles as SparkPool,
  Settings,
} from "lucide-react";

export const NAV_ITEMS = (t) => [
  { to: "/dashboard", label: t("common.home"), icon: House },
  { to: "/characters", label: t("common.characters"), icon: Users },
  { to: "/chats", label: t("common.chats") || "Chats", icon: MessageCircle },
  { to: "/spark_pool", label: t("common.spark_pool"), icon: SparkPool },
  { to: "/settings", label: t("common.settings"), icon: Settings },
];
