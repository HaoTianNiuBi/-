import { useId } from "react";
import { DatePicker } from "antd";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import "./date-field.css";

const isoFormat = "YYYY-MM-DD";
const inputFormats = ["YYYY年M月D日", isoFormat];

type DateFieldProps = {
  label: string;
  className?: string;
  value: string;
  onChange: (value: string) => void;
  min?: string;
  max?: string;
  required?: boolean;
  disabled?: boolean;
  tone?: "sage";
};

export function DateField({ label, className, value, onChange, min, max, required, disabled, tone }: DateFieldProps) {
  const id = useId();
  return <div className={className ? `qiyu-date-field ${className}` : "qiyu-date-field"}>
    <label htmlFor={id}>{label}</label>
    <DatePicker
      id={id}
      className={tone === "sage" ? "qiyu-date qiyu-date--sage" : "qiyu-date"}
      classNames={{ popup: { root: tone === "sage" ? "qiyu-calendar qiyu-calendar--sage" : "qiyu-calendar" } }}
      placeholder="选择日期"
      format={inputFormats}
      value={value ? dayjs(value) : null}
      onChange={(date) => onChange(date?.format(isoFormat) ?? "")}
      minDate={min ? dayjs(min) : undefined}
      maxDate={max ? dayjs(max) : undefined}
      aria-required={required}
      disabled={disabled}
    />
  </div>;
}
