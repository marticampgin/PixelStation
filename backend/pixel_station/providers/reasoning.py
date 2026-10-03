"""Remove model reasoning channels without buffering the generated explanation."""


class ReasoningFilter:
    names = ("think", "thinking", "reasoning", "analysis")
    tags = tuple(f"<{prefix}{name}>" for name in names for prefix in ("", "/"))

    def __init__(self, *, protect_json_strings: bool = False):
        self.protect_json_strings = protect_json_strings
        self.hidden: list[str] = []
        self.tag = ""
        self.marks = ""
        self.mark_at_line_start = False
        self.fence: tuple[str, int] | None = None
        self.inline_ticks = 0
        self.line_start = True
        self.line_spaces = 0
        self.escaped = False
        self.json_string = False
        self.json_escape = False

    def _write(self, text: str, output: list[str]) -> None:
        if self.hidden:
            return
        output.append(text)
        for character in text:
            if character in "\r\n":
                self.line_start, self.line_spaces = True, 0
            elif self.line_start and character == " " and self.line_spaces < 3:
                self.line_spaces += 1
            else:
                self.line_start = False
            self.escaped = character == "\\" and not self.escaped

    def _flush_marks(self, next_character: str, output: list[str]) -> None:
        marks, self.marks = self.marks, ""
        if self.fence:
            if (
                self.mark_at_line_start
                and marks[0] == self.fence[0]
                and len(marks) >= self.fence[1]
                and (not next_character or next_character.isspace())
            ):
                self.fence = None
        elif self.inline_ticks:
            if marks[0] == "`" and len(marks) == self.inline_ticks:
                self.inline_ticks = 0
        elif self.mark_at_line_start and len(marks) >= 3:
            self.fence = (marks[0], len(marks))
        elif marks[0] == "`":
            self.inline_ticks = len(marks)
        self._write(marks, output)

    def _consume(self, character: str, output: list[str]) -> None:
        if self.tag:
            self.tag += character
            normalized = self.tag.lower()
            if normalized in self.tags:
                closing = normalized.startswith("</")
                name = normalized[2:-1] if closing else normalized[1:-1]
                if not closing:
                    self.hidden.append(name)
                elif name in self.hidden:
                    # Mismatched nested closures fail closed until the matching outer channel ends.
                    while self.hidden:
                        if self.hidden.pop() == name:
                            break
                self.tag = ""
            elif not any(tag.startswith(normalized) for tag in self.tags):
                literal, self.tag = self.tag[:-1], ""
                self._write(literal, output)
                self._consume(character, output)
            return
        if self.marks:
            if character == self.marks[0]:
                self.marks += character
                return
            self._flush_marks(character, output)
        if self.hidden:
            if character == "<":
                self.tag = character
            return
        if self.json_string:
            self._write(character, output)
            if self.json_escape:
                self.json_escape = False
            elif character == "\\":
                self.json_escape = True
            elif character == '"':
                self.json_string = False
            return
        if self.protect_json_strings and character == '"':
            self.json_string = True
            self._write(character, output)
        elif (
            character == "`" or character == "~" and (self.line_start or self.fence)
        ) and not self.escaped:
            self.marks, self.mark_at_line_start = character, self.line_start
        elif character == "<" and not (self.fence or self.inline_ticks or self.escaped):
            self.tag = character
        else:
            self._write(character, output)

    def feed(self, content: str) -> str:
        output: list[str] = []
        for character in content:
            self._consume(character, output)
        return "".join(output)

    def finish(self) -> str:
        output: list[str] = []
        if self.marks:
            self._flush_marks("", output)
        # Incomplete reasoning tags and unterminated channels are never released.
        self.tag = ""
        return "".join(output)


def public_structured_content(content: str) -> str:
    filter = ReasoningFilter(protect_json_strings=True)
    return filter.feed(content) + filter.finish()
