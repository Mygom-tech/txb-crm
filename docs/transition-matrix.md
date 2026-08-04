# Opportunity transition matrix

Generated from `crm.txb.pipelines.actions`. Do not edit by hand.

## Delivering Coaching

| From | To | Action | Admin only |
| --- | --- | --- | --- |
| Active | Inactive | Mark Inactive | yes |
| Active | On Hold | Put on Hold | yes |
| Active | Payment Hold | Put on Payment Hold | yes |
| Contract Cleared | Active | Set First Call Date | yes |
| Inactive | Active | Reactivate | yes |
| On Hold | Active | Reactivate | yes |
| On Hold | Inactive | Mark Inactive | yes |
| Payment Hold | Active | Reactivate | yes |
| Payment Hold | Inactive | Mark Inactive | yes |
| Submitted | Waiting on Review | Move to Waiting on Review | yes |
| Waiting on Review | Contract Cleared | Clear Contract | yes |

## Workshop

| From | To | Action | Admin only |
| --- | --- | --- | --- |
| Lost | Workshop submitted | Reopen | no |
| Sold | Lost | Cancel Workshop | no |
| Sold | Lost | Mark as "Not Interested" | no |
| VCS call run | Lost | Cancel Workshop | no |
| VCS call run | Lost | Mark as "Not Interested" | no |
| VCS call run | Workshop set | Set Workshop | no |
| VCS call set | Lost | Cancel Workshop | no |
| VCS call set | Lost | Mark as "Not Interested" | no |
| VCS call set | VCS call run | Run VCS Call | no |
| VCS call set | Workshop rescheduling in progress | Reschedule | no |
| VCS call set | Workshop set | Run VCS Call | no |
| Workshop ran | Lost | Cancel Workshop | no |
| Workshop ran | Lost | Mark as "Not Interested" | no |
| Workshop ran | Sold | Won | no |
| Workshop rescheduling in progress | Lost | Cancel Workshop | no |
| Workshop rescheduling in progress | Lost | Mark as "Not Interested" | no |
| Workshop rescheduling in progress | Workshop set | Set Workshop | no |
| Workshop set | Lost | Run Workshop | no |
| Workshop set | Lost | Cancel Workshop | no |
| Workshop set | Lost | Mark as "Not Interested" | no |
| Workshop set | Workshop ran | Run Workshop | no |
| Workshop set | Workshop rescheduling in progress | Run Workshop | no |
| Workshop set | Workshop rescheduling in progress | Reschedule | no |
| Workshop submitted | Lost | Cancel Workshop | no |
| Workshop submitted | Lost | Mark as "Not Interested" | no |
| Workshop submitted | VCS call set | Set VCS Call | no |

## Individual Session

| From | To | Action | Admin only |
| --- | --- | --- | --- |
| Follow-up | Lost | Cancel a BAP | no |
| Follow-up | Lost | Mark as "Not Interested" | no |
| Follow-up | Session Set | Book a BAP | no |
| Lost | Submitted | Reopen | no |
| Session Run | Lost | Cancel a BAP | no |
| Session Run | Lost | Mark as "Not Interested" | no |
| Session Run | Won | Won | no |
| Session Set | Follow-up | Reschedule a BAP | no |
| Session Set | Lost | Cancel a BAP | no |
| Session Set | Lost | Mark as "Not Interested" | no |
| Session Set | Session Run | Run a BAP | no |
| Session Set | Won | Run a BAP | no |
| Submitted | Lost | Cancel a BAP | no |
| Submitted | Lost | Mark as "Not Interested" | no |
| Submitted | Session Set | Book a BAP | no |
| Won | Lost | Cancel a BAP | no |
| Won | Lost | Mark as "Not Interested" | no |

## Selling Training

| From | To | Action | Admin only |
| --- | --- | --- | --- |
| Contract signed | Training date set | Set Training Date | no |
| Contract signed | Training not interested | Mark as "Not Interested" | no |
| Training date set | Training not interested | Training Run | no |
| Training date set | Training not interested | Mark as "Not Interested" | no |
| Training date set | Training run | Training Run | no |
| Training discovery meeting run | Training not interested | Mark as "Not Interested" | no |
| Training discovery meeting run | Training proposal meeting set | Set Proposal Meeting | no |
| Training discovery meeting set | Training discovery meeting run | Run Discovery Meeting | no |
| Training discovery meeting set | Training not interested | Run Discovery Meeting | no |
| Training discovery meeting set | Training not interested | Mark as "Not Interested" | no |
| Training discovery meeting set | Training proposal submitted | Run Discovery Meeting | no |
| Training negotiations | Contract signed | Negotiation Result | no |
| Training negotiations | Contract signed | Contract Signed | no |
| Training negotiations | Training date set | Contract Signed | no |
| Training negotiations | Training not interested | Negotiation Result | no |
| Training negotiations | Training not interested | Mark as "Not Interested" | no |
| Training not interested | Training submitted | Reopen | no |
| Training proposal meeting run | Contract signed | Negotiation Result | no |
| Training proposal meeting run | Training negotiations | Negotiation Result | no |
| Training proposal meeting run | Training not interested | Negotiation Result | no |
| Training proposal meeting run | Training not interested | Mark as "Not Interested" | no |
| Training proposal meeting set | Training negotiations | Run Proposal Meeting | no |
| Training proposal meeting set | Training not interested | Run Proposal Meeting | no |
| Training proposal meeting set | Training not interested | Mark as "Not Interested" | no |
| Training proposal meeting set | Training proposal meeting run | Run Proposal Meeting | no |
| Training proposal submitted | Training not interested | Mark as "Not Interested" | no |
| Training proposal submitted | Training proposal meeting set | Set Proposal Meeting | no |
| Training run | Training not interested | Mark as "Not Interested" | no |
| Training submitted | Training discovery meeting set | Set Discovery Meeting | no |
| Training submitted | Training not interested | Mark as "Not Interested" | no |
