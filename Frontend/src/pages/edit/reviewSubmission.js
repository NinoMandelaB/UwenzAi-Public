// src/pages/edit/reviewSubmission.js
//
// Decides whether saving the edit form sends a character to admin review.
//
// Only the owner of a public character goes through review; admins set the
// status directly. Saving is the owner's way of submitting:
//   - approved  -> any change is re-reviewed (the server enforces this too)
//   - draft     -> first submission; a public draft is otherwise never seen by
//                  admins, because nothing else moves it into the queue
//   - rejected  -> resubmission after the owner fixed the problem
//   - pending   -> already in the queue, nothing to submit
//
// `reviewStatus` is the server's `review_status`. The serialized character's
// plain `status` field is the chat presence ("online"), not the review state.

const NO_REVIEW = Object.freeze({ kind: null, submitForReview: false });

export function getReviewSubmission({
  isAdmin,
  isOwner,
  reviewStatus,
  isPublic,
}) {
  if (isAdmin || !isOwner || !isPublic) return NO_REVIEW;

  switch (String(reviewStatus || "").toLowerCase()) {
    case "approved":
      return { kind: "reapproval", submitForReview: true };
    case "draft":
      return { kind: "draft", submitForReview: true };
    case "rejected":
      return { kind: "rejected", submitForReview: true };
    default:
      return NO_REVIEW;
  }
}
