import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import ReminderButton, {
  ReminderPanel as RealReminderPanel,
} from "./ReminderPanel";
const ReminderPanel = (props) => <RealReminderPanel canCreate {...props} />;
vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    user: { user_id: "u-1", timezone: "Africa/Nairobi" },
    sessionGeneration: 1,
  }),
}));

vi.mock("../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));

vi.mock("../services/reminderService", () => ({
  listReminders: vi.fn(),
  createReminder: vi.fn(),
  updateReminder: vi.fn(),
  deleteReminder: vi.fn(),
}));

const { listReminders, createReminder, updateReminder, deleteReminder } =
  await import("../services/reminderService");

const sampleReminder = {
  reminder_id: "r-1",
  character_id: "c-1",
  character_name: "Zuri",
  conversation_id: "conv-1",
  reminder_text: "Take medication",
  due_at: "2026-09-05T09:00:00+00:00",
  due_at_local: "2026-09-05 09:00",
  timezone: "Africa/Nairobi",
  status: "pending",
  notice_sent: false,
  created_at: "2026-09-04T10:00:00+00:00",
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ReminderButton", () => {
  it("renders a toggle button and opens the panel on click", async () => {
    listReminders.mockResolvedValue([]);
    render(<ReminderButton activeConvId="conv-1" charName="Zuri" />);

    expect(screen.queryByRole("dialog")).toBe(null);

    fireEvent.click(screen.getByTestId("reminder-panel-toggle"));

    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(listReminders).toHaveBeenCalledOnce();
  });
});

describe("ReminderPanel", () => {
  it("shows management-specific empty instructions without offering creation", async () => {
    listReminders.mockResolvedValue([]);
    render(<RealReminderPanel />);
    await screen.findByText(/No scheduled reminders. To create one/);
    expect(screen.queryByText(/Add one below/)).toBe(null);
    expect(screen.getByTestId("reminder-add-toggle").disabled).toBe(true);
    expect(createReminder).not.toHaveBeenCalled();
  });

  it("prevents creation until the initial list settles", async () => {
    let resolve;
    listReminders.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    render(<ReminderPanel activeConvId="conv-1" />);
    const add = screen.getByTestId("reminder-add-toggle");
    expect(add.disabled).toBe(true);
    fireEvent.click(add);
    expect(screen.queryByPlaceholderText(/Remind me to/)).toBe(null);
    expect(createReminder).not.toHaveBeenCalled();
    await act(async () => resolve([]));
    expect(add.disabled).toBe(false);
  });

  it("keeps scheduled reminder management when creation is off", async () => {
    listReminders.mockResolvedValue([sampleReminder]);
    deleteReminder.mockResolvedValue({
      reminder_id: sampleReminder.reminder_id,
    });
    render(<ReminderPanel activeConvId="conv-1" canCreate={false} />);
    await screen.findByText(sampleReminder.reminder_text);
    expect(screen.getByTestId("reminder-add-toggle").disabled).toBe(true);
    expect(screen.getByLabelText("Edit reminder").disabled).toBe(false);
    fireEvent.click(screen.getByLabelText("Delete reminder"));
    fireEvent.click(screen.getByText("Confirm"));
    await waitFor(() =>
      expect(screen.queryByText(sampleReminder.reminder_text)).toBe(null),
    );
  });

  it("sends only changed text for an overdue reminder, not its schedule", async () => {
    listReminders.mockResolvedValue([sampleReminder]);
    updateReminder.mockResolvedValue({
      ...sampleReminder,
      reminder_text: "Corrected text",
    });
    render(<ReminderPanel activeConvId="conv-1" />);
    fireEvent.click(await screen.findByLabelText("Edit reminder"));
    fireEvent.change(screen.getByDisplayValue(sampleReminder.reminder_text), {
      target: { value: "Corrected text" },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() =>
      expect(updateReminder).toHaveBeenCalledWith("r-1", {
        reminderText: "Corrected text",
      }),
    );
    expect(await screen.findByText("Corrected text")).toBeTruthy();
  });

  it("labels create with the profile zone and edit with the original snapshot zone", async () => {
    listReminders.mockResolvedValue([
      {
        ...sampleReminder,
        timezone: "America/New_York",
        due_at_local: "2026-11-10 09:00",
      },
    ]);
    render(<ReminderPanel activeConvId="conv-1" />);
    await screen.findByText(/America\/New_York/);
    fireEvent.click(screen.getByTestId("reminder-add-toggle"));
    expect(
      screen.getByLabelText("New reminder time (Africa/Nairobi)"),
    ).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Edit reminder"));
    expect(
      screen.getByLabelText("Reminder time (America/New_York)").value,
    ).toBe("09:00");
    expect(
      screen.getByLabelText("Reminder date (America/New_York)").value,
    ).toBe("2026-11-10");
  });

  it.each([400, 404, null])(
    "shows edit and delete errors with Add closed (status %s)",
    async (status) => {
      const error = Object.assign(new Error("Operation failed"), { status });
      listReminders.mockResolvedValue([sampleReminder]);
      updateReminder.mockRejectedValue(error);
      deleteReminder.mockRejectedValue(error);
      render(<ReminderPanel activeConvId="conv-1" />);
      fireEvent.click(await screen.findByLabelText("Edit reminder"));
      fireEvent.change(screen.getByDisplayValue(sampleReminder.reminder_text), {
        target: { value: "Keep this draft" },
      });
      fireEvent.click(screen.getByText("Save"));
      expect(await screen.findByRole("alert")).toBeTruthy();
      expect(screen.getByDisplayValue("Keep this draft")).toBeTruthy();
      fireEvent.click(screen.getByText("Cancel"));
      fireEvent.click(screen.getByLabelText("Delete reminder"));
      expect(screen.queryByRole("alert")).toBe(null);
      fireEvent.click(screen.getByText("Confirm"));
      expect(await screen.findByRole("alert")).toBeTruthy();
      expect(screen.queryByPlaceholderText(/Remind me to/)).toBe(null);
    },
  );

  it.each([
    { payload: [null] },
    { payload: [{ reminder_id: "bad" }] },
    { payload: [{ ...sampleReminder, due_at: {} }] },
  ])(
    "recovers from malformed records without a render crash",
    async ({ payload }) => {
      listReminders.mockResolvedValue(payload);
      render(<ReminderPanel activeConvId="conv-1" />);
      expect(await screen.findByRole("alert")).toBeTruthy();
      listReminders.mockResolvedValue([]);
      fireEvent.click(screen.getByText("Reload reminders"));
      expect(await screen.findByText(/No reminders yet/)).toBeTruthy();
    },
  );
  it("shows an empty state when there are no reminders", async () => {
    listReminders.mockResolvedValue([]);
    render(
      <ReminderPanel activeConvId="conv-1" charName="Zuri" onClose={vi.fn()} />,
    );

    expect(await screen.findByText(/No reminders yet/i)).toBeTruthy();
  });

  it("lists existing reminders with their local due time and character", async () => {
    listReminders.mockResolvedValue([sampleReminder]);
    render(
      <ReminderPanel activeConvId="conv-1" charName="Zuri" onClose={vi.fn()} />,
    );

    expect(await screen.findByText("Take medication")).toBeTruthy();
    expect(screen.getByText(/2026-09-05 09:00/)).toBeTruthy();
  });

  it("surfaces a load error instead of crashing", async () => {
    listReminders.mockRejectedValue(new Error("network_unreachable"));
    render(
      <ReminderPanel activeConvId="conv-1" charName="Zuri" onClose={vi.fn()} />,
    );

    expect(await screen.findByText("network_unreachable")).toBeTruthy();
  });

  it("creates a reminder against the active conversation", async () => {
    listReminders.mockResolvedValue([]);
    createReminder.mockResolvedValue({
      ...sampleReminder,
      reminder_id: "r-2",
    });
    render(
      <ReminderPanel activeConvId="conv-1" charName="Zuri" onClose={vi.fn()} />,
    );

    await screen.findByText(/No reminders yet/i);
    fireEvent.click(screen.getByTestId("reminder-add-toggle"));

    fireEvent.change(screen.getByPlaceholderText(/Remind me to/i), {
      target: { value: "Drink water" },
    });
    const [dateInput, timeInput] = screen
      .getAllByDisplayValue("")
      .filter((el) => el.tagName === "INPUT");
    fireEvent.change(dateInput, { target: { value: "2026-09-06" } });
    fireEvent.change(timeInput, { target: { value: "10:00" } });

    fireEvent.click(screen.getByText("Add reminder"));

    await waitFor(() =>
      expect(createReminder).toHaveBeenCalledWith({
        conversationId: "conv-1",
        reminderText: "Drink water",
        dueDate: "2026-09-06",
        dueTime: "10:00",
      }),
    );
  });

  it("disables adding a reminder when there is no active conversation", async () => {
    listReminders.mockResolvedValue([]);
    render(
      <ReminderPanel activeConvId={null} charName="Zuri" onClose={vi.fn()} />,
    );

    await screen.findByText(/No scheduled reminders/i);
    expect(screen.getByTestId("reminder-add-toggle").disabled).toBe(true);
  });

  it("edits a reminder", async () => {
    listReminders.mockResolvedValue([sampleReminder]);
    updateReminder.mockResolvedValue({
      ...sampleReminder,
      reminder_text: "Take medication (updated)",
      due_at_local: "2026-09-05 10:00",
    });
    render(
      <ReminderPanel activeConvId="conv-1" charName="Zuri" onClose={vi.fn()} />,
    );

    await screen.findByText("Take medication");
    fireEvent.click(screen.getByLabelText("Edit reminder"));

    const textarea = screen.getByDisplayValue("Take medication");
    fireEvent.change(textarea, {
      target: { value: "Take medication (updated)" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(updateReminder).toHaveBeenCalledWith("r-1", {
        reminderText: "Take medication (updated)",
      }),
    );
    expect(await screen.findByText("Take medication (updated)")).toBeTruthy();
  });

  it("deletes a reminder after confirming", async () => {
    listReminders.mockResolvedValue([sampleReminder]);
    deleteReminder.mockResolvedValue({ reminder_id: "r-1" });
    render(
      <ReminderPanel activeConvId="conv-1" charName="Zuri" onClose={vi.fn()} />,
    );

    await screen.findByText("Take medication");
    fireEvent.click(screen.getByLabelText("Delete reminder"));
    fireEvent.click(screen.getByText("Confirm"));

    await waitFor(() => expect(deleteReminder).toHaveBeenCalledWith("r-1"));
    await waitFor(() =>
      expect(screen.queryByText("Take medication")).toBe(null),
    );
  });

  it("calls onClose when the close button is clicked", async () => {
    listReminders.mockResolvedValue([]);
    const onClose = vi.fn();
    render(
      <ReminderPanel activeConvId="conv-1" charName="Zuri" onClose={onClose} />,
    );

    await screen.findByText(/No reminders yet/i);
    fireEvent.click(screen.getByLabelText("Close reminders"));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
