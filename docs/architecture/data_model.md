# Data Model Notes

The canonical model separates:

```text
Work -> conceptual paper
WorkVersion -> specific version
File -> physical file identity
FileSegment -> page range within file
FileWorkLink -> file/work many-to-many relationship
Location -> where file can be obtained
Reference -> bibliography item extracted from a work
CitationMention -> in-text citation context
Shelf -> collection of works
Rack -> collection of shelves
Tag -> label
MetadataAssertion -> provenance-aware metadata value
Summary -> extracted/local AI/external AI/human summary
TopicAssignment -> topic model output
AuditEvent -> auth/activity/change log
```

Do not simplify this into one PDF equals one paper.
