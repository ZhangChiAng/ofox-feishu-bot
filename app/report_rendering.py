"""Image rendering for structured model reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Protocol, TypeAlias

from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True, slots=True)
class TextBlock:
    """A titled text section in a report document."""

    title: str
    lines: list[str]


@dataclass(frozen=True, slots=True)
class TableBlock:
    """A titled table section in a report document."""

    title: str
    headers: list[str]
    rows: list[list[str]]
    note: str = ""


ReportBlock: TypeAlias = TextBlock | TableBlock


@dataclass(frozen=True, slots=True)
class ReportDocument:
    """Structured report content before image rendering."""

    title: str
    blocks: list[ReportBlock] = field(default_factory=list)


class ReportRenderer(Protocol):
    """Protocol for rendering report documents to PNG bytes."""

    def render(self, document: ReportDocument) -> bytes:
        """Renders a report document.

        Args:
            document: Structured report content.

        Returns:
            PNG bytes.
        """

        ...


@dataclass(frozen=True, slots=True)
class TableLayout:
    """Computed table dimensions used during rendering."""

    column_widths: list[int]
    row_lines: list[list[list[str]]]
    row_heights: list[int]
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class TextLayout:
    """Computed text block dimensions used during rendering."""

    wrapped_lines: list[str]
    width: int
    height: int


class PillowReportRenderer:
    """Renders model reports as compact PNG images."""

    def __init__(
        self,
        font_path: Path,
        *,
        max_width: int = 1200,
        margin: int = 40,
    ) -> None:
        """Initializes the image renderer.

        Args:
            font_path: Path to a TrueType/OpenType font that can display Chinese.
            max_width: Maximum output image width in pixels.
            margin: Outer image padding in pixels.
        """

        self.font_path = font_path
        self.max_width = max_width
        self.margin = margin
        self.title_font = ImageFont.truetype(str(font_path), 30)
        self.section_font = ImageFont.truetype(str(font_path), 22)
        self.body_font = ImageFont.truetype(str(font_path), 17)
        self.small_font = ImageFont.truetype(str(font_path), 15)

    def render(self, document: ReportDocument) -> bytes:
        """Renders a report document into PNG bytes."""

        draw = _measure_draw()
        content_width = self.max_width - (self.margin * 2)
        elements: list[tuple[str, object, int]] = []

        title_height = _text_height(self.title_font) + 18
        total_height = self.margin + title_height
        used_width = _text_width(draw, document.title, self.title_font)

        for block in document.blocks:
            if isinstance(block, TextBlock):
                layout = self._layout_text_block(draw, block, content_width)
                elements.append(("text", (block, layout), layout.height))
                total_height += layout.height
                used_width = max(used_width, layout.width)
            else:
                layout = self._layout_table(draw, block, content_width)
                elements.append(("table", (block, layout), layout.height))
                total_height += layout.height
                used_width = max(
                    used_width,
                    layout.width,
                    _text_width(draw, block.title, self.section_font),
                )

        image_width = min(self.max_width, max(520, used_width + (self.margin * 2)))
        image_height = total_height + self.margin
        image = Image.new("RGB", (image_width, image_height), "white")
        draw = ImageDraw.Draw(image)

        y = self.margin
        draw.text(
            (self.margin, y), document.title, font=self.title_font, fill="#111827"
        )
        y += title_height

        for kind, payload, _height in elements:
            if kind == "text":
                block, layout = payload
                y = self._draw_text_block(draw, block, layout, self.margin, y)
            else:
                block, layout = payload
                y = self._draw_table(draw, block, layout, self.margin, y)

        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def _layout_text_block(
        self,
        draw: ImageDraw.ImageDraw,
        block: TextBlock,
        content_width: int,
    ) -> TextLayout:
        line_height = _text_height(self.body_font) + 7
        wrapped_lines: list[str] = []
        for line in block.lines:
            wrapped_lines.extend(_wrap_text(draw, line, self.body_font, content_width))

        text_widths = [
            _text_width(draw, block.title, self.section_font),
            *(_text_width(draw, line, self.body_font) for line in wrapped_lines),
        ]
        height = (
            _text_height(self.section_font)
            + 14
            + (len(wrapped_lines) * line_height)
            + 20
        )
        return TextLayout(
            wrapped_lines=wrapped_lines,
            width=max(text_widths),
            height=height,
        )

    def _draw_text_block(
        self,
        draw: ImageDraw.ImageDraw,
        block: TextBlock,
        layout: TextLayout,
        x: int,
        y: int,
    ) -> int:
        draw.text((x, y), block.title, font=self.section_font, fill="#1f2937")
        y += _text_height(self.section_font) + 12
        for line in layout.wrapped_lines:
            draw.text((x, y), line, font=self.body_font, fill="#374151")
            y += _text_height(self.body_font) + 7
        return y + 20

    def _layout_table(
        self,
        draw: ImageDraw.ImageDraw,
        block: TableBlock,
        content_width: int,
    ) -> TableLayout:
        column_count = len(block.headers)
        cell_x_padding = 13
        cell_y_padding = 9
        min_column_width = 70
        max_column_width = 340
        available_content_width = content_width - (cell_x_padding * 2 * column_count)

        natural_widths = [
            _column_natural_width(draw, block, index, self.body_font, self.section_font)
            for index in range(column_count)
        ]
        column_widths = [
            min(max_column_width, max(min_column_width, width))
            for width in natural_widths
        ]
        _shrink_columns(column_widths, available_content_width, min_column_width)

        row_lines: list[list[list[str]]] = []
        row_heights: list[int] = []
        for row in [block.headers, *block.rows]:
            wrapped_row = [
                _wrap_text(draw, cell, self.body_font, column_widths[index])
                for index, cell in enumerate(row)
            ]
            line_count = max(len(lines) for lines in wrapped_row)
            row_lines.append(wrapped_row)
            row_heights.append(
                (line_count * (_text_height(self.body_font) + 4)) + (cell_y_padding * 2)
            )

        table_width = sum(column_widths) + (cell_x_padding * 2 * column_count)
        table_height = sum(row_heights)
        if block.note:
            note_lines = _wrap_text(draw, block.note, self.small_font, table_width)
            table_height += 8 + (len(note_lines) * (_text_height(self.small_font) + 5))

        height = _text_height(self.section_font) + 12 + table_height + 24
        return TableLayout(
            column_widths=column_widths,
            row_lines=row_lines,
            row_heights=row_heights,
            width=table_width,
            height=height,
        )

    def _draw_table(
        self,
        draw: ImageDraw.ImageDraw,
        block: TableBlock,
        layout: TableLayout,
        x: int,
        y: int,
    ) -> int:
        cell_x_padding = 13
        cell_y_padding = 9

        draw.text((x, y), block.title, font=self.section_font, fill="#1f2937")
        y += _text_height(self.section_font) + 12

        table_x = x
        for row_index, wrapped_row in enumerate(layout.row_lines):
            row_height = layout.row_heights[row_index]
            fill = "#f3f4f6" if row_index == 0 else "white"
            draw.rectangle(
                [table_x, y, table_x + layout.width, y + row_height],
                fill=fill,
                outline="#e5e7eb",
            )

            cell_x = table_x
            for column_index, cell_lines in enumerate(wrapped_row):
                column_width = layout.column_widths[column_index]
                text_x = cell_x + cell_x_padding
                text_y = y + cell_y_padding
                for line in cell_lines:
                    draw.text(
                        (text_x, text_y),
                        line,
                        font=self.body_font,
                        fill="#111827" if row_index == 0 else "#374151",
                    )
                    text_y += _text_height(self.body_font) + 4
                cell_x += column_width + (cell_x_padding * 2)
                draw.line([(cell_x, y), (cell_x, y + row_height)], fill="#e5e7eb")

            y += row_height

        if block.note:
            y += 8
            for line in _wrap_text(draw, block.note, self.small_font, layout.width):
                draw.text((table_x, y), line, font=self.small_font, fill="#6b7280")
                y += _text_height(self.small_font) + 5

        return y + 24


def _measure_draw() -> ImageDraw.ImageDraw:
    image = Image.new("RGB", (1, 1), "white")
    return ImageDraw.Draw(image)


def _column_natural_width(
    draw: ImageDraw.ImageDraw,
    block: TableBlock,
    index: int,
    body_font: ImageFont.FreeTypeFont,
    header_font: ImageFont.FreeTypeFont,
) -> int:
    values = [block.headers[index], *(row[index] for row in block.rows)]
    return max(
        _text_width(draw, value, header_font if offset == 0 else body_font)
        for offset, value in enumerate(values)
    )


def _shrink_columns(
    widths: list[int],
    available_width: int,
    min_width: int,
) -> None:
    """Shrinks columns in place until the table fits the target width."""

    while sum(widths) > available_width:
        shrinkable = [index for index, width in enumerate(widths) if width > min_width]
        if not shrinkable:
            break
        overage = sum(widths) - available_width
        step = max(1, (overage + len(shrinkable) - 1) // len(shrinkable))
        for index in shrinkable:
            widths[index] = max(min_width, widths[index] - step)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: object,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    cleaned = str(text).strip() or "-"
    if _text_width(draw, cleaned, font) <= max_width:
        return [cleaned]

    tokens = cleaned.split(" ")
    if len(tokens) == 1:
        tokens = list(cleaned)

    lines: list[str] = []
    current = ""
    separator = "" if len(tokens[0]) == 1 else " "
    for token in tokens:
        candidate = token if not current else f"{current}{separator}{token}"
        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = token

    if current:
        lines.append(current)
    return lines or ["-"]


def _text_width(
    draw: ImageDraw.ImageDraw,
    text: object,
    font: ImageFont.FreeTypeFont,
) -> int:
    bbox = draw.textbbox((0, 0), str(text), font=font)
    return bbox[2] - bbox[0]


def _text_height(font: ImageFont.FreeTypeFont) -> int:
    bbox = font.getbbox("Ag")
    return bbox[3] - bbox[1]
