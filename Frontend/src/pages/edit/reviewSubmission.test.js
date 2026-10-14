import { describe, expect, it } from "vitest";
import { getReviewSubmission } from "./reviewSubmission";

const owner = { isAdmin: false, isOwner: true, isPublic: true };

describe("getReviewSubmission", () => {
  it("submits a public draft when its owner saves", () => {
    expect(getReviewSubmission({ ...owner, reviewStatus: "draft" })).toEqual({
      kind: "draft",
      submitForReview: true,
    });
  });

  it("resubmits a rejected public character", () => {
    expect(getReviewSubmission({ ...owner, reviewStatus: "rejected" })).toEqual(
      { kind: "rejected", submitForReview: true },
    );
  });

  it("re-reviews an approved public character", () => {
    expect(getReviewSubmission({ ...owner, reviewStatus: "approved" })).toEqual(
      { kind: "reapproval", submitForReview: true },
    );
  });

  it("does nothing for a character that is already pending", () => {
    expect(
      getReviewSubmission({ ...owner, reviewStatus: "pending" })
        .submitForReview,
    ).toBe(false);
  });

  it("never reviews a private character", () => {
    for (const reviewStatus of ["draft", "rejected", "approved"]) {
      expect(
        getReviewSubmission({ ...owner, isPublic: false, reviewStatus })
          .submitForReview,
      ).toBe(false);
    }
  });

  it("leaves admins to set the status themselves", () => {
    expect(
      getReviewSubmission({ ...owner, isAdmin: true, reviewStatus: "draft" })
        .submitForReview,
    ).toBe(false);
  });

  it("ignores users who do not own the character", () => {
    expect(
      getReviewSubmission({ ...owner, isOwner: false, reviewStatus: "draft" })
        .submitForReview,
    ).toBe(false);
  });

  it("is case-insensitive and tolerates a missing status", () => {
    expect(getReviewSubmission({ ...owner, reviewStatus: "DRAFT" }).kind).toBe(
      "draft",
    );
    expect(getReviewSubmission({ ...owner }).submitForReview).toBe(false);
  });
});
