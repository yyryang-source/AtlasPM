"""Strict untagged SEC cover-table parser. Observations, not identities.

Never infer a ticker from an issuer name, a narrative mention, a two-column
legacy Section 12(b) table, or a different row. No interval inference.
"""
from html.parser import HTMLParser
import re

HEADERS=(
    {"title of each class","title of each class of securities"},
    {"trading symbol","trading symbols","trading symbol(s)"},
    {"name of each exchange on which registered","name of each exchange on which securities are registered"},
)


class LegacyTables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth=0
        self.tables=[]
        self.table=None
        self.row=None
        self.cell=None
        self.bad=False
        self.skip=0

    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=="table":
            self.depth+=1
            if self.depth==1:
                self.table=[]
                self.bad=False
            else:
                self.bad=True
        if self.depth!=1:
            return
        if tag in ("script","style","ix:hidden"):
            self.skip+=1
            self.bad=True
        if tag.startswith("ix:") or "hidden" in a or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden",a.get("style") or "",re.I):
            self.bad=True
        if tag=="tr":
            if self.row is not None:
                self.bad=True
            self.row=[]
        if tag in ("td","th"):
            if self.row is None or self.cell is not None:
                self.bad=True
            else:
                if any(a.get(k,"1")!="1" for k in ("rowspan","colspan")):
                    self.bad=True
                self.cell=[]

    def handle_data(self,data):
        if self.cell is not None and self.depth==1 and not self.skip:
            self.cell.append(data)

    def handle_endtag(self,tag):
        if self.depth==1:
            if tag in ("script","style","ix:hidden") and self.skip:
                self.skip-=1
            if tag in ("td","th") and self.cell is not None:
                if self.row is not None:
                    self.row.append(" ".join("".join(self.cell).split()))
                self.cell=None
            if tag=="tr" and self.row is not None:
                if self.cell is not None:
                    self.bad=True
                self.table.append(self.row)
                self.row=None
                self.cell=None
        if tag=="table" and self.depth:
            self.depth-=1
            if not self.depth:
                if not self.bad and self.row is None and self.cell is None and self.table:
                    self.tables.append(self.table)
                self.table=self.row=self.cell=None
                self.skip=0


def rows(primary):
    """Only exact three-column headers; no fuzzy column or name matching."""
    p=LegacyTables()
    p.feed(primary)
    p.close()
    output=[]
    for table in p.tables:
        if len(table)<2 or any(len(r)!=3 for r in table):
            continue
        header=[x.lower().rstrip(":") for x in table[0]]
        if not all(value in allowed for value,allowed in zip(header,HEADERS)):
            continue
        output.extend(table[1:])
    return output
