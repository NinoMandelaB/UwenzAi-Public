import { useLanguage } from "../../context/LanguageContext";
import {
  TextInput,
  TextArea,
  CategoryPills,
} from "../character/CharacterFormFields";

export default function EditBasicInfoSection({ form, updateField, errors }) {
  const { t } = useLanguage();

  return (
    <div className="space-y-5 rounded-2xl border p-4 theme-card">
      <h3 className="text-base font-semibold theme-text">
        {t("characters.basicInformation")}
      </h3>

      <TextInput
        label={t("characters.characterName")}
        value={form.name}
        onChange={(e) => updateField("name", e.target.value)}
        placeholder={t("wizard.namePlaceholder")}
        maxLength={150}
        error={errors.name}
        required
      />
      <TextArea
        label={t("characters.shortDescription")}
        value={form.short_description}
        onChange={(e) => updateField("short_description", e.target.value)}
        placeholder={t("characters.shortDescriptionPlaceholder")}
        rows={2}
        maxLength={255}
        error={errors.short_description}
        required
      />
      <TextInput
        label={t("characters.mood")}
        value={form.mood}
        onChange={(e) => updateField("mood", e.target.value)}
        placeholder={t("characters.moodPlaceholder")}
        maxLength={80}
        error={errors.mood}
        required
      />
      <CategoryPills
        value={form.category}
        onChange={(v) => updateField("category", v)}
        error={errors.category}
      />
      <TextArea
        label={t("characters.intro")}
        value={form.intro}
        onChange={(e) => updateField("intro", e.target.value)}
        placeholder={t("characters.introPlaceholder")}
        rows={3}
        maxLength={255}
        error={errors.intro}
        required
      />
    </div>
  );
}
