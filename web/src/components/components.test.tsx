import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Alert } from "./Alert";
import { Button } from "./Button";
import { TextField } from "./TextField";

describe("Button", () => {
  it("calls its handler when clicked", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Save</Button>);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onClick).toHaveBeenCalledOnce();
  });

  it("is disabled and busy while loading, so a form cannot be submitted twice", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Signing in
      </Button>,
    );

    const button = screen.getByRole("button", { name: "Signing in" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");

    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("defaults to type=button, so it never submits a form by accident", () => {
    render(<Button>Cancel</Button>);

    expect(screen.getByRole("button", { name: "Cancel" })).toHaveAttribute("type", "button");
  });
});

describe("TextField", () => {
  it("labels its input", () => {
    render(<TextField label="Work email" type="email" />);

    expect(screen.getByLabelText("Work email")).toHaveAttribute("type", "email");
  });

  it("describes the input with its hint", () => {
    render(<TextField label="Password" hint="At least 12 characters." />);

    expect(screen.getByLabelText("Password")).toHaveAccessibleDescription("At least 12 characters.");
  });

  it("replaces the hint with the error and marks the input invalid", () => {
    render(<TextField label="Password" hint="At least 12 characters." error="Too short." />);

    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Too short.");
    expect(screen.queryByText("At least 12 characters.")).not.toBeInTheDocument();
  });
});

describe("Alert", () => {
  it("announces errors immediately", () => {
    render(<Alert tone="error">Incorrect email or password.</Alert>);

    expect(screen.getByRole("alert")).toHaveTextContent("Incorrect email or password.");
  });

  it("announces other messages politely", () => {
    render(<Alert tone="info">Analysis is queued.</Alert>);

    expect(screen.getByRole("status")).toHaveTextContent("Analysis is queued.");
  });
});
