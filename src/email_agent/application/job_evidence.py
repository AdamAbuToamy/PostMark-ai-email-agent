"""Conservative employment evidence; employer identity alone never proves a job."""
from dataclasses import dataclass
from email.utils import parseaddr
import re


@dataclass(frozen=True)
class JobEvidence:
    state: str
    label: str
    reason: str


LABELS = {'applied': 'Applied / Received', 'interview': 'Interview',
          'rejected': 'Rejected', 'offer': 'Offer', 'unknown': 'Unknown'}
ADVERTISEMENT = r'\b(job alerts?|jobs? for you|we are hiring|career opportunities|hiring now|apply now|recommended jobs?|interview (?:tips|training|course)|career webinar)\b|דרושים|وظائف شاغرة'


def employment_evidence(subject: str, sender: str = '', body: str = '', verified: bool = False) -> JobEvidence | None:
    """Require a response about *your* application plus hiring context.

    Cached body is bounded and only used for ambiguity, never for external action.
    Ignore quoted replies and disclaimers to avoid borrowing old outcomes.
    """
    subject = subject.casefold()
    from email_agent.application.message_text import purpose_text
    excerpt = purpose_text(body)
    text = subject + '\n' + excerpt
    if re.search(ADVERTISEMENT, subject) or re.search(r'\b(?:admission|university application|scholarship|loan application|visa application|application error|software application|mock interview)\b', text):
        return None
    address = parseaddr(sender)[1].casefold()
    hiring_sender = bool(re.search(r'(?:recruit|career|talent|jobs|hiring|(?:^|[._-])hr[.@_-])', address) or re.search(r'@(?:[^@]+\.)?(?:greenhouse\.io|greenhouse-mail\.io|lever\.co|myworkday\.com|myworkdayjobs\.com|comeet\.co|comeet-recruit\.com|smartrecruiters\.com)$', address))
    context = hiring_sender or bool(re.search(r'\b(job|employment|position|role|candidate|recruitment|hiring team|applying for|applied for)\b|משרה|מועמד|وظيف|توظيف|العمل|المسمى الوظيفي', text))
    submitted = bool(re.search(r'\b(your application|application (?:was |has been )?(?:received|submitted)|thank you for (?:applying|your (?:job )?application)|thanks for applying|you applied|you have applied)\b|מועמדותך|קיבלנו את (?:מועמדותך|קורות החיים)|בקשתך למשרה|استلمنا طلب توظيفك|طلبك للوظيفة|طلب التوظيف الذي قدمته|תודה (?:על|שהגשת) מועמדות|בקשת המועמדות שלך', text))
    # A paired acknowledgment proves submission without an invented reference.
    paired = bool(re.search(r'\b(?:thank you|thanks) for applying\b', text) and re.search(r'\bapplication (?:received|submitted)\b', text))
    conditional_submission = bool(re.search(r'\bif you (?:have |already )?(?:completed|submitted)|\bif you are still (?:working on|completing) (?:the|your) application',excerpt))
    if conditional_submission and not verified and not paired:
        return None
    if not (verified or (submitted and context) or paired):
        return None
    if re.search(r'\b(not (?:an? |yet )?(?:employment |job )?offer|no offer|offer (?:withdrawn|rescinded)|interview (?:cancelled|canceled))\b', text):
        return JobEvidence('unknown', LABELS['unknown'], 'submitted_application_response_uncertain_outcome')
    if re.search(r'\b(?:not (?:been )?(?:rejected|declined)|not unsuccessful|no decision|no (?:hiring )?decision|(?:may|might|could) be (?:rejected|accepted)|not yet (?:accepted|selected))\b|טרם התקבלה החלטה|לא התקבלה החלטה|لم (?:يتم اتخاذ قرار|نتخذ قرار)|ليس (?:رفضًا|عرض عمل)|لم يتم رفض',text):
        return JobEvidence('unknown',LABELS['unknown'],'submitted_application_no_outcome')
    if re.search(r'\b(?:have )?decided to (?:pursue|continue with|select) other candidates\b',text):
        return JobEvidence('rejected',LABELS['rejected'],'explicit_submitted_application_rejected')
    patterns = [
        ('rejected', r'\byour application (?:was|has been|is) not accepted\b|\b(?:unable|not able|cannot) to offer you.{0,50}(?:position|role|employment)|\bdecided to (?:move forward|proceed) with other candidates\b|בקשר למועמדותך.{0,120}לא נמשיך|החלטנו (?:שלא|לא) להתקדם|לא התקבלת למשרה|לא נמשיך (?:בתהליך|עם מועמדותך)|لم يتم قبول (?:طلبك|ترشيحك)|لن نتمكن من المضي قدمًا|نعتذر.{0,70}(?:طلب|توظيف)|تم رفض طلبك للوظيفة|\b(not (?:be )?(?:moving|proceeding) (?:forward|with)|not selected|not successful|unsuccessful|(?:chosen|selected|moving forward with) other candidates|unable to (?:proceed|offer)|will not (?:be )?(?:proceed|progress)|application.{0,25}(?:rejected|declined))\b|לצערנו.{0,70}(?:מועמד|להמשיך)|לא נוכל להתקדם|نأسف.{0,60}(?:طلب|توظيف)'),
        ('offer', r'מועמדותך (?:התקבלה למשרה|אושרה לתפקיד)|התקבלת (?:למשרה|לתפקיד)|تم قبولك (?:في|لشغل) الوظيفة|يسرنا أن نقدم لك.{0,50}(?:وظيفة|منصب)|\byou have been selected for (?:the |a )?(?:position|role)\b|\b(?:pleased|delighted|happy|would like) to offer you.{0,80}(?:position|role|employment)|\b(?:your|attached|enclosed) (?:employment |job )?offer letter\b|\b(?:employment|job) offer for you\b|הצעת עבודה עבורך|يسرنا.{0,40}عرض.{0,25}وظيف'),
        ('interview', r'دعوتك.{0,30}(?:مقابلة|للمقابلة)|להזמין אותך לראיון|\b(?:invite you|invitation|schedule your|your upcoming|confirm your|interview (?:invitation|confirmation))\b.{0,80}\binterview\b|\binterview (?:invitation|confirmation|with our team)\b|זימון לראיון|دعوة.{0,20}مقابلة'),
        ('applied', r'תודה על הגשת מועמדותך|تم استلام طلبك للوظيفة|نشكر لك التقدم.{0,30}وظيفة|\byour application.{0,80}\b(?:was|has been) received\b|\b(?:thank you|thanks) for applying\b|\bapplication (?:was |has been )?(?:received|submitted)\b|\bwe(?:\x27re| are) currently reviewing (?:your (?:application|resume)|resumes with our hiring manager)\b|קיבלנו את (?:מועמדותך|קורות החיים)|استلمنا طلب توظيفك'),
    ]
    matched=[state for state,pattern in patterns if re.search(pattern,text)]
    if 'rejected' in matched and 'offer' in matched:
        return JobEvidence('unknown',LABELS['unknown'],'submitted_application_conflicting_outcomes')
    if matched:
        state=matched[0]
        return JobEvidence(state, LABELS[state], 'explicit_submitted_application_' + state)
    return JobEvidence('unknown', LABELS['unknown'], 'explicit_submitted_application_response')


def job_status(subject: str, sender: str = '', body: str = '', verified: bool = False) -> dict:
    evidence = employment_evidence(subject, sender, body, verified)
    return dict(state=evidence.state, label=evidence.label) if evidence else dict(state='unknown', label=LABELS['unknown'])
