//
// Created by manuelli on 2/10/17.
//

#include "footstep_plan.h"
#include "utils/geometry_util.h"
#include <stdexcept>

namespace plan_eval{

//Footstep
Footstep::Footstep(const drc::footstep_t & msg) {
  if (msg.is_right_foot){
    this->side_ = Side::RIGHT;
  } else{
    this->side_ = Side::LEFT;
  }
  pose_ = plan_eval::utils::IsometryFromPosition3dMsg(msg.pos);
}

Side Footstep::getSide() const {
  return this->side_;
}

const Eigen::Isometry3d & Footstep::getPose() const {
  return this->pose_;
}


// FootstepPlan
FootstepPlan::FootstepPlan(std::vector <drc::footstep_t> footstep_msgs) {
  this->footstep_msgs_ = footstep_msgs;
  this->num_footsteps_ = footstep_msgs.size();
  this->next_footstep_idx_ = 0;

  footsteps_.resize(num_footsteps_);
  for(int i = 0; i < num_footsteps_; i++){
    footsteps_[i] = std::shared_ptr<Footstep>(new Footstep(footstep_msgs[i]));
  }
}

void FootstepPlan::incrementCounter() {
  std::cout << "FootstepPlan incremetCounter() called \n";
  next_footstep_idx_++;
}

std::shared_ptr<Footstep> FootstepPlan::getNextFootstep() {
  return footsteps_.at(next_footstep_idx_);
}

std::shared_ptr<const Footstep> FootstepPlan::getNextFootstep() const{
  return footsteps_[next_footstep_idx_];
}

const drc::footstep_t & FootstepPlan::getNextFootstepMsg() const{
  return this->footstep_msgs_.at(next_footstep_idx_);
}

bool FootstepPlan::hasNextFootstep() const {
  bool has_next_footstep = (next_footstep_idx_ < this->footsteps_.size());
  return has_next_footstep;
}

Side FootstepPlan::sideOfNextFootstep() const {
  if (!this->hasNextFootstep()){
    throw std::out_of_range("there are no more footsteps");
  }
  return this->footsteps_[next_footstep_idx_]->getSide();
}

void FootstepPlan::printDebugInfo() const {
  std::cout << "--------FootstepPlan Debug Info------- \n";
  std::cout << "num footsteps " << footsteps_.size() << std::endl;
  std::cout << "next footstep idx " << next_footstep_idx_ << std::endl;
  std::cout << "------------------------------ \n \n \n";
}


}// plan_eval