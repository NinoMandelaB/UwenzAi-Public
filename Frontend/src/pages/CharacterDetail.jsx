// src/pages/CharacterDetail.jsx

import { useEffect, useState, useCallback, useRef } from "react";
import { clearOperationId, reuseOperationId } from "../utils/idempotency";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  MapPin,
  Activity,
  MessageCircle,
  Edit,
  ChevronDown,
  Flag,
  Briefcase,
  ImageIcon,
  Loader2,
} from "lucide-react";
import PrimaryButton from "../components/PrimaryButton";
import {
  getCharacterById,
  generateGalleryImage,
  listGalleryImages,
  flagCharacter,
  listHobbies,
} from "../services/characterService";
import { listPersonalities } from "../services/personalityService";
import { getCharacterMessage } from "../utils/characterMessageMapper";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useChatModal } from "../context/ChatModalContext";
import { deleteGalleryImage } from "../services/characterService";

// ---------- Extracted components ----------
import CharacterGalleryCarousel from "./details/CharacterGalleryCarousel";
import CharacterLightbox from "./details/CharacterLightbox";
import CharacterReportModal from "./details/CharacterReportModal";
import CharacterQuickInfo from "./details/CharacterQuickInfo";
import CharacterIntro from "./details/CharacterIntro";
import CharacterAppearance from "./details/CharacterAppearance";
import CharacterPersonality from "./details/CharacterPersonality";
import CharacterLifestyle from "./details/CharacterLifestyle";
import CharacterRelationship from "./details/CharacterRelationship";
import CharacterPersona from "./details/CharacterPersona";
import { SectionTitle } from "./details/shared";
import UserPreferences from "./details/UserPreferences"; // <-- NEW

// ---------- Utility helpers ----------
function getReadableCategory(category, t) {
  if (!category) return "—";
  const map = {
    supportive: t("characters.supportive"),
    fun: t("characters.fun"),
    romantic: t("characters.romantic"),
  };
  return map[category.toLowerCase()] || category;
}

function getReadableLanguage(lang, t) {
  if (!lang) return "—";
  const map = {
    en: t("common.english"),
    sw: t("common.swahili"),
    sh: "Sheng",
    multi: "Multilingual",
  };
  return map[lang.toLowerCase()] || lang.toUpperCase();
}

function Toast({ message, onClose, type = "info" }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);

  const bgColor =
    type === "error"
      ? "bg-rose-500/90"
      : type === "success"
        ? "bg-emerald-500/90"
        : "bg-gray-800/90";

  return (
    <div
      className={`fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full px-5 py-3 text-sm text-white shadow-lg ${bgColor} backdrop-blur`}
    >
      {message}
    </div>
  );
}

export default function CharacterDetail() {
  const { characterId } = useParams();
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { user, isAdmin, isAuthenticated, isLoading: authLoading } = useAuth();

  // Gated on auth restoration as well as auth itself, so a refresh never
  // flashes the control before the session is known. Staff/admins are
  // authenticated users and so are included.
  const canGenerateImage = !authLoading && isAuthenticated;
  const { openChat } = useChatModal();
  const swiperRef = useRef(null);

  const [refreshingGallery, setRefreshingGallery] = useState(false);
  const [character, setCharacter] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [galleryImages, setGalleryImages] = useState([]);
  const [generating, setGenerating] = useState(false);

  // One key per generation attempt, reused if that attempt is retried, so a
  // request the server already charged for is never paid for twice.
  const galleryOperationRef = useRef(null);
  const [pendingSlideTo, setPendingSlideTo] = useState(null);

  // Accordion
  const [showMore, setShowMore] = useState(false);

  // Lightbox
  const [lightboxIndex, setLightboxIndex] = useState(null);

  // Carousel
  const [currentImageIndex, setCurrentImageIndex] = useState(0);

  // Flag modal
  const [showFlagModal, setShowFlagModal] = useState(false);
  const [flagReason, setFlagReason] = useState("");
  const [flagging, setFlagging] = useState(false);
  const [flagError, setFlagError] = useState("");

  // Toast
  const [toast, setToast] = useState(null);
  const showToast = (msg, type = "info") => setToast({ message: msg, type });
  const dismissToast = () => setToast(null);

  // Maps
  const [hobbyMap, setHobbyMap] = useState({});
  const [personalityMap, setPersonalityMap] = useState({});

  // Fetch character + gallery.
  // Wait for auth to finish initializing before requesting the gallery: the
  // in-memory access token is restored asynchronously on load, and the gallery
  // list endpoint only returns the viewer's own PRIVATE images (e.g. images
  // generated in chat) when the request is authenticated. Firing before the
  // token is ready would send an anonymous request and silently drop them.
  // Re-run when auth state flips so private images appear right after login.
  useEffect(() => {
    if (authLoading) return;
    async function fetchData() {
      setLoading(true);
      setError(null);
      try {
        const [charRes, galleryRes] = await Promise.all([
          getCharacterById(characterId),
          listGalleryImages(characterId),
        ]);
        setCharacter(charRes?.data?.character || null);
        setGalleryImages((galleryRes?.data?.images || []).filter(Boolean));
      } catch (err) {
        setError(getCharacterMessage(err, t("characters.loadError")));
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, [characterId, t, authLoading, isAuthenticated]);

  // Fetch maps
  useEffect(() => {
    async function fetchMaps() {
      try {
        const [hobbyRes, persRes] = await Promise.all([
          listHobbies(),
          listPersonalities(),
        ]);
        const hMap = {};
        hobbyRes?.data?.hobbies?.forEach((h) => (hMap[h.id] = h.name));
        setHobbyMap(hMap);
        const pMap = {};
        persRes?.data?.personalities?.forEach((p) => (pMap[p.id] = p.name));
        setPersonalityMap(pMap);
      } catch (e) {
        console.error("Failed to load maps:", e);
      }
    }
    fetchMaps();
  }, []);

  // Combined images
  const allImages = character
    ? [
        character.avatar_url,
        ...galleryImages.filter(Boolean).map((img) => img.image_url),
      ].filter(Boolean)
    : [];

  // Lightbox callbacks
  const openLightbox = (idx) => setLightboxIndex(idx);
  const closeLightbox = () => setLightboxIndex(null);
  const prevImage = useCallback(() => {
    setLightboxIndex((prev) => (prev === 0 ? allImages.length - 1 : prev - 1));
  }, [allImages.length]);
  const nextImage = useCallback(() => {
    setLightboxIndex((prev) => (prev === allImages.length - 1 ? 0 : prev + 1));
  }, [allImages.length]);

  const handleDeleteGalleryImage = async (imageId) => {
    try {
      await deleteGalleryImage(imageId);
      setGalleryImages((prev) =>
        prev.filter((img) => img && img.id !== imageId),
      );
      showToast(t("characters.imageDeleted"), "success");
    } catch (err) {
      showToast(err?.message || t("characters.imageDeleteFailed"), "error");
    }
  };

  // Chat – now uses the modal
  const handleStartChat = (e) => {
    e?.stopPropagation();
    const characterData = {
      id: character.character_id,
      name: character.name,
      avatar: character.avatar_url,
      role: character.short_description,
    };
    openChat(characterData);
  };

  // Generate image
  const handleGenerate = async (extraPrompt = "") => {
    // Defensive: the control is hidden for guests, but a stale handler must not
    // fire an unauthenticated request if the session expires mid-interaction.
    if (!canGenerateImage) return;
    setGenerating(true);
    try {
      await generateGalleryImage(characterId, {
        prompt: "portrait, high quality, detailed",
        extra_prompt: extraPrompt,
        operationId: reuseOperationId(galleryOperationRef, extraPrompt),
      });
      clearOperationId(galleryOperationRef);
      showToast(t("characters.imageGenerated"), "success");

      setRefreshingGallery(true);
      const galleryRes = await listGalleryImages(characterId);
      const freshImages = (galleryRes?.data?.images || []).filter(Boolean);
      setGalleryImages(freshImages);

      setPendingSlideTo(freshImages.length);
    } catch (err) {
      showToast(err?.message || t("characters.imageGenerateFailed"), "error");
    } finally {
      setGenerating(false);
      setRefreshingGallery(false);
    }
  };

  // Flag
  const handleFlagSubmit = async () => {
    if (!flagReason.trim()) return;
    setFlagging(true);
    setFlagError("");
    try {
      await flagCharacter(characterId, flagReason.trim());
      setShowFlagModal(false);
      setFlagReason("");
      showToast(t("characters.reportReceived"), "success");
    } catch (err) {
      if (err?.data === "already_flagged") {
        setFlagError(t("characters.alreadyReported"));
      } else {
        setFlagError(t("characters.reportFailed"));
      }
    } finally {
      setFlagging(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="animate-spin text-cyan-400" size={32} />
      </div>
    );
  }

  if (error || !character) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-6 text-center">
        <p className="text-sm font-medium theme-text">
          {error || t("characters.characterNotFound")}
        </p>
        <PrimaryButton onClick={() => navigate("/characters")} className="mt-4">
          {t("characters.backToCharacters")}
        </PrimaryButton>
      </div>
    );
  }

  const categoryLabel = getReadableCategory(character.category, t);
  const languageLabel = getReadableLanguage(character.primary_language, t);
  const isOwner = user && character.created_by?.user_id === user.user_id;
  const canEdit = isAdmin || isOwner;
  const isActive = character.is_active;

  return (
    <div className="max-w-4xl pb-6">
      {/* Back button */}
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-2 text-sm font-medium theme-text-muted hover:underline px-4 pt-4"
      >
        <ArrowLeft size={16} /> {t("common.back")}
      </button>

      {/*
        Top section: on mobile we stack everything (unchanged).
        On desktop (md+) we put the gallery on the left and the
        name/info/actions/memory/intro on the right. The wrapper
        uses md:flex so mobile behaviour is identical to before.
        md:px-4 gives the desktop two-column row its own horizontal
        padding because the children no longer carry px-4 on md+.
      */}
      <div className="md:flex md:items-start md:gap-8 md:px-4">
        {/* Left column on desktop: Gallery Carousel */}
        <div className="md:flex-shrink-0">
          <CharacterGalleryCarousel
            allImages={allImages}
            galleryImages={galleryImages}
            characterName={character.name}
            currentImageIndex={currentImageIndex}
            onSlideChange={setCurrentImageIndex}
            onImageClick={openLightbox}
            generating={generating}
            onGenerate={handleGenerate}
            swiperRef={swiperRef}
            currentUserId={user?.user_id}
            isAdmin={isAdmin}
            onDeleteImage={handleDeleteGalleryImage}
            pendingSlideTo={pendingSlideTo}
            onSlideToDone={() => setPendingSlideTo(null)}
            refreshing={refreshingGallery}
            canGenerateImage={canGenerateImage}
          />
        </div>

        {/* Right column on desktop: name, info, actions, intro */}
        <div className="md:flex-1 md:min-w-0 md:mt-6">
          {/* Name, role, location, age, occupation */}
          <div className="px-4 md:px-0 mt-6 md:mt-0 space-y-2">
            <h1 className="text-2xl font-bold theme-text">{character.name}</h1>
            <p className="text-sm theme-text-muted">
              {character.short_description}
            </p>

            <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm theme-text-muted">
              {(character.town || character.country) && (
                <span className="flex items-center gap-1">
                  <MapPin size={14} />
                  {character.town && character.country
                    ? `${character.town}, ${character.country}`
                    : character.country || character.town}
                </span>
              )}
              <span className="flex items-center gap-1">
                <Activity size={14} />
                {t("characters.ageWithYears", { age: character.age })}
              </span>
              {character.occupation?.name && (
                <span className="flex items-center gap-1">
                  <Briefcase size={14} />
                  {character.occupation.name}
                </span>
              )}
            </div>
          </div>

          {/* Action buttons */}
          <div className="px-4 md:px-0 mt-6 flex flex-wrap items-center gap-3">
            {isActive && (
              <button
                onClick={handleStartChat}
                className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90"
                style={{
                  backgroundColor: "var(--color-accent-soft)",
                  color: "var(--color-accent)",
                  border: "1px solid var(--color-accent-soft)",
                }}
              >
                <MessageCircle size={16} />
                {t("characters.startChat")}
              </button>
            )}
            {canEdit && (
              <button
                onClick={() => navigate(`/characters/${characterId}/edit`)}
                className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition"
                style={{
                  backgroundColor: "var(--color-soft)",
                  color: "var(--color-text)",
                  border: "1px solid var(--color-border)",
                }}
              >
                <Edit size={16} />
                {t("common.edit")}
              </button>
            )}
            <button
              onClick={() => setShowFlagModal(true)}
              className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition"
              style={{
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text)",
                border: "1px solid var(--color-border)",
              }}
            >
              <Flag size={16} />
              {t("characters.report")}
            </button>
          </div>

          {/* ── NEW: User Preferences (Interactive Chat & Memory) ── */}
          <UserPreferences characterId={characterId} />

          {/* Intro */}
          <div className="px-4 md:px-0 mt-6">
            <CharacterIntro
              intro={character.intro}
              isPublic={character.is_public}
            />
          </div>
        </div>
        {/* end right column */}
      </div>
      {/* end two-column top section */}

      {/* More details accordion */}
      <div className="px-4 mt-6">
        <button
          onClick={() => setShowMore(!showMore)}
          className="flex w-full items-center justify-between rounded-xl border p-4 theme-card"
        >
          <span className="text-sm font-semibold theme-text">
            {t("characters.moreDetails")}
          </span>
          <ChevronDown
            size={20}
            className={`transition-transform ${showMore ? "rotate-180" : ""}`}
            style={{ color: "var(--color-accent)" }}
          />
        </button>
      </div>

      {showMore && (
        <div className="px-4 mt-4 space-y-6">
          <CharacterQuickInfo
            mood={character.mood}
            category={categoryLabel}
            language={languageLabel}
            status={character.review_status}
          />
          <CharacterAppearance
            gender={character.gender}
            visualStyle={character.visual_style}
            age={character.age}
            skinColor={character.skin_color}
            eyeColor={character.eye_color}
            hairLength={character.hair_length}
            hairstyle={character.hairstyle}
            hairColor={character.hair_color}
            bodyType={character.body_type}
            breastSize={character.breast_size}
            buttSize={character.butt_size}
            chestHair={character.chest_hair}
            muscleDefinition={character.muscle_definition}
            beardStyle={character.beard_style}
          />
          <CharacterPersonality
            personalityIds={character.personality_ids || []}
            personalityMap={personalityMap}
          />
          <CharacterLifestyle
            occupation={character.occupation}
            hobbyIds={character.hobby_ids || []}
            hobbyMap={hobbyMap}
          />
          <CharacterRelationship
            relationshipStyle={character.relationship_style}
            clothingStyle={character.clothing_style}
          />
          <CharacterPersona
            personalityDescription={character.personality_description}
            greetingMessage={character.greeting_message}
            systemInstructions={character.system_instructions}
            isAdmin={isAdmin}
          />
          {allImages.length > 1 && (
            <div>
              <SectionTitle
                icon={
                  <ImageIcon
                    size={18}
                    style={{ color: "var(--color-accent)" }}
                  />
                }
                title={t("characters.gallery")}
              />
              <div className="grid grid-cols-3 sm:grid-cols-4 gap-3 mt-2">
                {allImages.map((url, idx) => (
                  <button
                    key={idx}
                    onClick={() => openLightbox(idx)}
                    className="rounded-xl overflow-hidden border border-white/10 cursor-pointer hover:opacity-90 transition"
                  >
                    <img
                      src={url}
                      alt={`${t("characters.image")} ${idx + 1}`}
                      className="w-full h-24 object-cover"
                    />
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Lightbox */}
      <CharacterLightbox
        images={allImages}
        index={lightboxIndex}
        onClose={closeLightbox}
        onPrev={prevImage}
        onNext={nextImage}
      />

      {/* Report modal */}
      <CharacterReportModal
        show={showFlagModal}
        reason={flagReason}
        error={flagError}
        submitting={flagging}
        onReasonChange={setFlagReason}
        onSubmit={handleFlagSubmit}
        onClose={() => {
          setShowFlagModal(false);
          setFlagError("");
          setFlagReason("");
        }}
      />

      {/* Toast */}
      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onClose={dismissToast}
        />
      )}
    </div>
  );
}
