import {
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

interface FieldChrome {
  label: string;
  hint?: ReactNode;
  error?: string;
  className?: string;
}

function describedBy(
  id: string,
  hint: ReactNode | undefined,
  error: string | undefined,
): string | undefined {
  const ids: string[] = [];
  if (hint) ids.push(`${id}-hint`);
  if (error) ids.push(`${id}-error`);
  return ids.length > 0 ? ids.join(" ") : undefined;
}

function FieldMessages({
  id,
  hint,
  error,
}: {
  id: string;
  hint: ReactNode | undefined;
  error: string | undefined;
}) {
  return (
    <>
      {hint ? (
        <p className="field-hint" id={`${id}-hint`}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className="field-error" id={`${id}-error`} role="alert">
          {error}
        </p>
      ) : null}
    </>
  );
}

type TextFieldProps = FieldChrome & InputHTMLAttributes<HTMLInputElement>;

export function TextField({
  label,
  hint,
  error,
  className,
  required,
  id,
  ...props
}: TextFieldProps) {
  const generated = useId();
  const fieldId = id ?? generated;
  return (
    <div className={["field", className ?? ""].filter(Boolean).join(" ")}>
      <label className="field-label" htmlFor={fieldId}>
        {label}
        {required ? (
          <span className="field-required" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      <input
        {...props}
        id={fieldId}
        required={required}
        className={["input", error ? "input--invalid" : ""].filter(Boolean).join(" ")}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(fieldId, hint, error)}
      />
      <FieldMessages id={fieldId} hint={hint} error={error} />
    </div>
  );
}

type TextAreaFieldProps = FieldChrome & TextareaHTMLAttributes<HTMLTextAreaElement>;

export function TextAreaField({
  label,
  hint,
  error,
  className,
  required,
  id,
  ...props
}: TextAreaFieldProps) {
  const generated = useId();
  const fieldId = id ?? generated;
  return (
    <div className={["field", className ?? ""].filter(Boolean).join(" ")}>
      <label className="field-label" htmlFor={fieldId}>
        {label}
        {required ? (
          <span className="field-required" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      <textarea
        {...props}
        id={fieldId}
        required={required}
        className={["input", "textarea", error ? "input--invalid" : ""]
          .filter(Boolean)
          .join(" ")}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(fieldId, hint, error)}
      />
      <FieldMessages id={fieldId} hint={hint} error={error} />
    </div>
  );
}

type SelectFieldProps = FieldChrome &
  SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode };

export function SelectField({
  label,
  hint,
  error,
  className,
  required,
  id,
  children,
  ...props
}: SelectFieldProps) {
  const generated = useId();
  const fieldId = id ?? generated;
  return (
    <div className={["field", className ?? ""].filter(Boolean).join(" ")}>
      <label className="field-label" htmlFor={fieldId}>
        {label}
        {required ? (
          <span className="field-required" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      <select
        {...props}
        id={fieldId}
        required={required}
        className={["input", "select", error ? "input--invalid" : ""]
          .filter(Boolean)
          .join(" ")}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(fieldId, hint, error)}
      >
        {children}
      </select>
      <FieldMessages id={fieldId} hint={hint} error={error} />
    </div>
  );
}

interface CheckboxFieldProps {
  label: string;
  hint?: ReactNode;
  error?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}

export function CheckboxField({
  label,
  hint,
  error,
  checked,
  onChange,
  disabled,
}: CheckboxFieldProps) {
  const generated = useId();
  return (
    <div className="checkbox-field">
      <label className="checkbox-label" htmlFor={generated}>
        <input
          id={generated}
          type="checkbox"
          className="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          aria-describedby={
            hint || error ? `${generated}-description` : undefined
          }
        />
        <span>{label}</span>
      </label>
      {hint || error ? (
        <div id={`${generated}-description`}>
          {hint ? <p className="field-hint">{hint}</p> : null}
          {error ? (
            <p className="field-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
