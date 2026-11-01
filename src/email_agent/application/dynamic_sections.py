"""Corpus-driven local sections, independent of category names and persistence.

Existing category-specific semantic detectors are compatibility seeds, not the
section registry. Only observed sections are exposed. Repeated subject concepts
work in every user-created category; sender/thread alone cannot create a group.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import re
from email.utils import parseaddr
from email_agent.application.smart_sections import SECTIONS, section_for
from email_agent.application.job_evidence import employment_evidence

STOP = set('a an the and or to from for of in on at with your you we our is are was were has have been this that it re fw fwd update notification message new hello dear please thank thanks regards application received'.split())


@dataclass(frozen=True)
class SectionDiscovery:
    assignments: dict[str, str]
    names: dict[str, str]
    reasons: dict[str, str]
    source: str = 'local_topics'


def _tokens(subject):
    subject = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+', ' ', subject.casefold())
    subject = re.sub(r'\b[\w-]*\d[\w-]*\b', ' ', subject)
    return [word for word in re.findall(r'[^\W\d_]{3,}', subject) if word not in STOP][:32]


def discover_sections(category: str, rows: list[dict], profile=None) -> SectionDiscovery:
    """One section per message. Pure bounded analysis, no writes/provider calls.

    Manual category decisions are already reflected in the supplied membership.
    Labels derive from stable semantic IDs or a repeated phrase, never senders or
    a representative subject selected at random. New arrivals cannot rename an ID.
    """
    from email_agent.application.semantic_mail import semantic_sections,usable_frame
    semantic=semantic_sections(rows,profile)
    # A sparse sample must not replace the sections of an entire corpus.
    coverage=sum(bool(usable_frame(row.get('semantic'))) for row in rows)/max(1,len(rows))
    if semantic and (coverage>=.5 or (profile or {}).get('source')=='user_approved'):
        return SectionDiscovery(semantic['assignments'],semantic['names'],{key:'Observed '+semantic['dimension']+' dimension' for key in semantic['names']},'semantic_attributes')
    assignments, names, reasons = {}, {}, {}
    candidates = defaultdict(list)
    pending = []
    labels = dict(SECTIONS.get(category, ()))
    for row in rows:
        subject, sender, body = row['subject'], row.get('sender', ''), row.get('body', '')
        # Transitional detectors preserve shipped/pickup/delivered distinctions.
        seed = section_for(category, subject, sender, body)
        if category not in SECTIONS:
            # Semantic vocabularies can seed any new category; its identifier
            # does not determine which kinds of mail it is allowed to contain.
            context = subject.casefold() + ' ' + body.casefold()
            if re.search(r'\b(order|parcel|package|shipment|refund)\b|חבילה|הזמנ|طلبك|شحن',context):
                seed = section_for('orders',subject,sender,body)
                if seed != 'other':labels[seed] = dict(SECTIONS['orders'])[seed]
        if category == 'job_applications':
            reason = row.get('reason', '')
            verified = reason.startswith(('explicit_submitted_application_', 'verified_submitted_application_thread')) or 'Submitted application:' in reason
            job = employment_evidence(subject, sender, body, verified)
            seed = job.state if job and job.state != 'unknown' else 'other'
            if seed != 'other':labels[seed] = job.label
            else:
                from email_agent.application.semantic_mail import supported_application_frame,status_key,JOB_LABELS
                if supported_application_frame(row.get('semantic') or {}):
                    seed=status_key(row['semantic']['status']);labels[seed]=JOB_LABELS[seed]
        if seed != 'other':
            assignments[row['message_id']] = seed
            names[seed] = labels.get(seed, seed.replace('_', ' ').title())
            reasons[seed] = 'Local content / metadata topic; not an AI organization decision'
            continue
        tokens = _tokens(subject)
        # Repeated adjacent concepts provide purpose, unlike a sender-only group.
        phrases = set(zip(tokens, tokens[1:]))
        pending.append((row, phrases))
        for phrase in phrases:
            candidates[phrase].append(row)
    viable = {}
    for phrase, members in candidates.items():
        if len(members) < 2:
            continue
        # Multiple messages in one thread are valid recurring context; a single
        # message/sender never is. Domain diversity is supporting evidence only.
        domains = {parseaddr(row.get('sender', ''))[1].rsplit('@', 1)[-1] for row in members}
        threads = {row.get('thread_id') or row['message_id'] for row in members}
        viable[phrase] = (len(members), len(domains), len(threads))
    # Keep a small useful set of recurring topics rather than one chip for each
    # repeated sender/template. Unselected evidence remains visible in Other.
    viable = dict(sorted(viable.items(),key=lambda entry:(-entry[1][0],-entry[1][1],entry[0]))[:8])
    for row, phrases in pending:
        choices = [phrase for phrase in phrases if phrase in viable]
        if choices:
            # Lexical identity is stable; corpus frequency breaks ties only for
            # this message's membership, never edits another section's name.
            phrase = min(choices, key=lambda p: (-viable[p][0], -viable[p][1], -viable[p][2], p))
            label = ' '.join(phrase).capitalize()
            key = 'topic_' + hashlib.sha256(' '.join(phrase).encode()).hexdigest()[:16]
            assignments[row['message_id']] = key
            names[key] = label
            reasons[key] = 'Repeated subject concepts, supported by sender and thread context'
        else:
            assignments[row['message_id']] = 'other'
    # A topic must still contain >=2 members after resolving overlapping phrases.
    counts = Counter(assignments.values())
    for id, key in list(assignments.items()):
        if key.startswith('topic_') and counts[key] < 2:
            assignments[id] = 'other'
    used = set(assignments.values())
    names = {key: name for key, name in names.items() if key in used}
    if 'other' in used:
        names['other'] = 'Other'
        reasons['other'] = 'No sufficiently supported recurring concept'
    return SectionDiscovery(assignments, names, reasons)
