#include "AffordanceRelation.h"

using namespace action_authoring;

/**todo: Comment here
   @param affordance1
   @param affordance2
   @param relationType*/
AffordanceRelation::AffordanceRelation(AffConstPtr affordance1, 
				       AffConstPr affordance2, 
				       const AffordanceRelation::RelationType &relationType) 
  : m_affordance1(affordance1), 
    m_affordance2(affordance2), m_relationType(RelationType)
{

}
