import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useMemo,
} from "react";
import { STORAGE_KEYS } from "../config/constants";

const WizardContext = createContext(null);

const STEPS = [
  { id: 1, name: "Style", path: "style" },
  { id: 2, name: "Appearance", path: "appearance" },
  { id: 3, name: "Hair", path: "hair" },
  { id: 4, name: "Body", path: "body" },
  { id: 5, name: "Personality", path: "personality" },
  { id: 6, name: "Lifestyle", path: "lifestyle" },
  { id: 7, name: "Relationship", path: "relationship" },
  { id: 8, name: "Review", path: "review" },
];

const DEFAULT_STATE = {
  // Step 1: Basic & Location
  name: "",
  gender: "female",
  visual_style: "realistic",
  country: "",
  town: "",
  is_public: true,

  // Step 2: Age & Skin
  age: 25,
  skin_color: "medium",
  eye_color: "brown",

  // Step 3: Hair
  hair_length: "medium",
  hairstyle: "straight",
  hair_color: "brown",

  // Step 4: Body
  body_type: "average",
  breast_size: "medium",
  butt_size: "medium",
  chest_hair: "na",
  muscle_definition: "na",
  beard_style: "na",

  // Step 5: Personality
  personality: "friendly",

  // Step 6: Occupation & Hobbies
  occupation_id: null,
  hobby_ids: [],

  // Step 7: Relationship & Clothing
  relationship_style: "companion",
  clothing_style: "casual",

  // Step 8: Avatar (optional, admin only)
  avatarFile: null,
  avatarPreview: null,
};

export function WizardProvider({ children }) {
  const [state, setState] = useState(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEYS.CHARACTER_DRAFT);
      return saved ? JSON.parse(saved) : DEFAULT_STATE;
    } catch {
      return DEFAULT_STATE;
    }
  });

  const [currentStep, setCurrentStep] = useState(1);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [createdCharacterId, setCreatedCharacterId] = useState(null);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.CHARACTER_DRAFT, JSON.stringify(state));
  }, [state]);

  const updateState = useCallback((updates) => {
    setState((prev) => ({ ...prev, ...updates }));
  }, []);

  const clearDraft = useCallback(() => {
    localStorage.removeItem(STORAGE_KEYS.CHARACTER_DRAFT);
    setState(DEFAULT_STATE);
    setCurrentStep(1);
    setError(null);
  }, []);

  const nextStep = useCallback(() => {
    if (currentStep < STEPS.length) {
      setCurrentStep((prev) => prev + 1);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, [currentStep]);

  const prevStep = useCallback(() => {
    if (currentStep > 1) {
      setCurrentStep((prev) => prev - 1);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, [currentStep]);

  const goToStep = useCallback((step) => {
    if (step >= 1 && step <= STEPS.length) {
      setCurrentStep(step);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, []);

  const value = useMemo(
    () => ({
      STEPS,
      state,
      updateState,
      currentStep,
      nextStep,
      prevStep,
      goToStep,
      isSubmitting,
      setIsSubmitting,
      error,
      setError,
      createdCharacterId,
      setCreatedCharacterId,
      clearDraft,
    }),
    [
      STEPS,
      state,
      updateState,
      currentStep,
      nextStep,
      prevStep,
      goToStep,
      isSubmitting,
      error,
      createdCharacterId,
      clearDraft,
    ],
  );

  return (
    <WizardContext.Provider value={value}>{children}</WizardContext.Provider>
  );
}

export function useWizard() {
  const context = useContext(WizardContext);
  if (!context) {
    throw new Error("useWizard must be used within WizardProvider");
  }
  return context;
}
