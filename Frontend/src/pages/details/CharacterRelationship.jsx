import { Heart } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";
import { SectionTitle, AttributeRow } from "./shared";
import { CLOTHING_LABEL_KEYS } from "../../config/clothingStyles";

const RELATIONSHIP_DISPLAY = {
  friend: () => "wizard.relationshipFriend",
  boyfriend: () => "wizard.relationshipBoyfriend",
  girlfriend: () => "wizard.relationshipGirlfriend",
  mentor: () => "wizard.relationshipMentor",
  companion: () => "wizard.relationshipCompanion",
  flirty: () => "wizard.relationshipFlirty",
  romantic: () => "wizard.relationshipRomantic",
};

const CLOTHING_DISPLAY = Object.fromEntries(
  Object.entries(CLOTHING_LABEL_KEYS).map(([value, key]) => [value, () => key]),
);

function formatChoice(value, displayMap, t) {
  if (!value) return "—";
  const getter = displayMap[value];
  if (typeof getter === "function") {
    const key = getter();
    return t(key);
  }
  return getter || value.replace(/_/g, " ");
}

export default function CharacterRelationship({
  relationshipStyle,
  clothingStyle,
}) {
  const { t } = useLanguage();

  return (
    <>
      <SectionTitle
        icon={<Heart size={18} style={{ color: "var(--color-accent)" }} />}
        title={t("characters.relationshipAndStyle")}
      />
      <div
        className="rounded-2xl border p-4 space-y-2"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-card)",
        }}
      >
        <AttributeRow
          label={t("characters.relationship")}
          value={formatChoice(relationshipStyle, RELATIONSHIP_DISPLAY, t)}
        />
        <AttributeRow
          label={t("characters.clothing")}
          value={formatChoice(clothingStyle, CLOTHING_DISPLAY, t)}
        />
      </div>
    </>
  );
}
