import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useWizard, WizardProvider } from "../context/WizardContext";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { ArrowLeft, ArrowRight } from "lucide-react";

// Step Components (unchanged)
import StepBasicInfo from "../components/wizard/StepBasicInfo";
import StepAppearance from "../components/wizard/StepAppearance";
import StepHair from "../components/wizard/StepHair";
import StepBody from "../components/wizard/StepBody";
import StepPersonality from "../components/wizard/StepPersonality";
import StepLifestyle from "../components/wizard/StepLifestyle";
import StepRelationship from "../components/wizard/StepRelationship";
import StepReview from "../components/wizard/StepReview";

// Validation functions (unchanged)
function validateStep1(state) {
  if (!state.name?.trim()) return "name_required";
  return null;
}

function validateStep2(state) {
  if (!state.age || state.age < 18 || state.age > 100)
    return "age_out_of_range";
  return null;
}

function validateStep3(_state) {
  return null;
}

function validateStep4(_state) {
  return null;
}

function validateStep5(state) {
  if (!state.personality) return "invalid_personality";
  return null;
}

function validateStep6(_state) {
  return null;
}

function validateStep7(_state) {
  return null;
}

const VALIDATORS = {
  1: validateStep1,
  2: validateStep2,
  3: validateStep3,
  4: validateStep4,
  5: validateStep5,
  6: validateStep6,
  7: validateStep7,
};

// --- NEW: Compact Step Indicator ---
function StepProgress({ currentStep, totalSteps, stepName }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span
          className="text-sm font-semibold"
          style={{ color: "var(--color-accent)" }}
        >
          Step {currentStep}
        </span>
        <span className="text-sm theme-text-muted">of {totalSteps}</span>
      </div>
      <span className="text-sm font-medium theme-text">{stepName}</span>
    </div>
  );
}

function WizardContent() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { user } = useAuth();
  const { STEPS, state, currentStep, nextStep, prevStep, error, setError } =
    useWizard();

  useEffect(() => {
    if (!user) {
      navigate("/signin");
    }
  }, [user, navigate]);

  const handleNext = () => {
    const validator = VALIDATORS[currentStep];
    if (validator) {
      const validationError = validator(state);
      if (validationError) {
        setError(validationError);
        return;
      }
    }
    setError(null);
    nextStep();
  };

  const handleCancel = () => {
    if (window.confirm(t("wizard.cancelConfirm") || "Discard changes?")) {
      navigate("/characters");
    }
  };

  const renderStep = () => {
    switch (currentStep) {
      case 1:
        return <StepBasicInfo />;
      case 2:
        return <StepAppearance />;
      case 3:
        return <StepHair />;
      case 4:
        return <StepBody />;
      case 5:
        return <StepPersonality />;
      case 6:
        return <StepLifestyle />;
      case 7:
        return <StepRelationship />;
      case 8:
        return <StepReview />;
      default:
        return <StepBasicInfo />;
    }
  };

  const isLastStep = currentStep === STEPS.length;
  const currentStepData = STEPS.find((s) => s.id === currentStep);

  if (!user) return null;

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold theme-text">
          {t("wizard.title") || "Create Character"}
        </h1>
        <div className="mt-1">
          <StepProgress
            currentStep={currentStep}
            totalSteps={STEPS.length}
            stepName={currentStepData?.name || ""}
          />
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {t(`errors.${error}`) || error}
        </div>
      )}

      {/* Step Content */}
      <div>{renderStep()}</div>

      {/* Navigation Buttons (hidden on review step) */}
      {!isLastStep && (
        <div className="flex items-center justify-between pt-2">
          <button
            type="button"
            onClick={prevStep}
            disabled={currentStep === 1}
            className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition disabled:opacity-40"
            style={{
              backgroundColor: "var(--color-soft)",
              color: "var(--color-text)",
              border: "1px solid var(--color-border)",
            }}
          >
            <ArrowLeft size={16} />
            {t("wizard.previous") || "Previous"}
          </button>

          <button
            type="button"
            onClick={handleCancel}
            className="text-sm font-medium theme-text-muted hover:underline"
          >
            {t("common.cancel") || "Cancel"}
          </button>

          <button
            type="button"
            onClick={handleNext}
            className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90"
            style={{
              backgroundColor: "var(--color-accent)",
              color: "#082f49",
            }}
          >
            {t("wizard.next") || "Next"}
            <ArrowRight size={16} />
          </button>
        </div>
      )}
    </div>
  );
}

export default function CreateCharacter() {
  return (
    <WizardProvider>
      <WizardContent />
    </WizardProvider>
  );
}
