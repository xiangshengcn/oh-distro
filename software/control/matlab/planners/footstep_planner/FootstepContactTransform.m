classdef FootstepContactTransform < CoordinateTransform
  % Transform from 6-dof footstep pose expressed at foot origin to a particular
  % contact location on the foot (like the center of the sole). This lets us
  % work with footstep positions expressed as poses of the center of the foot
  % when it's convenient but then ensure that we publish footsteps expressed
  % as the origin of the foot.
  %
  % Note: this is trivially equivalent to constructing a RigidBody for every
  % footstep and then using forwardKin and inverseKin to swap between center
  % poses and origin poses, but that seems like overkill just to apply a
  % single transform.
  methods
    function obj = FootstepContactTransform(from,to,T)
      obj=obj@CoordinateTransform(from,to,true,true);
      typecheck(from,'CoordinateFrame');
      typecheck(to,'CoordinateFrame');
      obj.T = T;
    end

    function x = output(obj,~,~,y)
      M = rpy2rotmat(y(4:6));
      H = [M, reshape(y(1:3), 3,1);
           0 0 0 1];
      X = H * obj.T;
      x = [X(1:3,end); rotmat2rpy(X(1:3,1:3))];
      % d = M * obj.contact_offset;
      % x = [y(1:3) + d(1:3); y(4:6)];
    end
  end

  properties
    T
  end
end
