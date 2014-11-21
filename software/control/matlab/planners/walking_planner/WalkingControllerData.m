classdef WalkingControllerData
  properties
    S
    s1
    s2
    s1dot
    s2dot
    support_times
    supports
    comtraj
    mu
    t_offset
    link_constraints
    zmptraj
    qtraj
    ignore_terrain
  end

  methods
    function obj = WalkingControllerData(V, support_times,...
                                         supports, comtraj, mu, t_offset,...
                                         link_constraints, zmptraj, qtraj,...
                                         ignore_terrain)
      obj.S = V.S;
      obj.s1 = V.s1;
      obj.s2 = V.s2;
      obj.s1dot = fnder(obj.s1,1);
      obj.s2dot = fnder(obj.s2,1);
      obj.support_times = support_times;
      obj.supports = supports;
      obj.comtraj = comtraj;
      obj.mu = mu;
      obj.t_offset = t_offset;
      
      obj.link_constraints = link_constraints;
      obj.zmptraj = zmptraj;
      obj.qtraj = qtraj;
      obj.ignore_terrain = ignore_terrain;
    end

    function msg = toLCM(obj)
      msg = drc.walking_plan_t();

      msg.robot_name = 'atlas';
      msg.utime = 0;
      msg.qtraj = mxSerialize(obj.qtraj);
      msg.n_qtraj_bytes = length(msg.qtraj);

      msg.S = mxSerialize(obj.S);
      msg.n_S_bytes = length(msg.S);

      msg.s1 = mxSerialize(obj.s1);
      msg.n_s1_bytes = length(msg.s1);

      msg.s1dot = mxSerialize(obj.s1dot);
      msg.n_s1dot_bytes = length(msg.s1dot);

      msg.s2 = mxSerialize(obj.s2);
      msg.n_s2_bytes = length(msg.s2);

      msg.s2dot = mxSerialize(obj.s2dot);
      msg.n_s2dot_bytes = length(msg.s2dot);

      msg.n_support_times = length(obj.support_times);
      msg.support_times = obj.support_times;

      msg.supports = mxSerialize(obj.supports);
      msg.n_supports_bytes = length(msg.supports);

      msg.comtraj = mxSerialize(obj.comtraj);
      msg.n_comtraj_bytes = length(msg.comtraj);

      msg.zmptraj = mxSerialize(obj.zmptraj);
      msg.n_zmptraj_bytes = length(msg.zmptraj);

      msg.link_constraints = mxSerialize(obj.link_constraints);
      msg.n_link_constraints_bytes = length(msg.link_constraints);

      msg.mu = mean(obj.mu);
      msg.ignore_terrain = obj.ignore_terrain;
      if isfield(obj,'t_offset')
        msg.t_offset = obj.t_offset;
      else
        msg.t_offset = 0;
      end
    end
  end

  methods (Static = true)
    function obj = from_drake_walking_data(data, qstar)
      t_offset = 0;
      ignore_terrain = false;
      obj = WalkingControllerData(data.V, data.support_times, data.supports, data.comtraj, data.mu, t_offset, data.link_constraints, data.zmptraj, qstar, ignore_terrain);
    end

    function obj = from_walking_plan_t(msg_data)

      S = mxDeserialize(msg_data.S);

      if isa(S,'Trajectory')
        S = fasteval(S,0); % S is always constant
      end

      s1 = mxDeserialize(msg_data.s1);
%       s1dot = mxDeserialize(msg_data.s1dot);
      s2 = mxDeserialize(msg_data.s2, 'uint8');
%       s2dot = mxDeserialize(msg_data.s2dot);
      supports = mxDeserialize(msg_data.supports);
      if iscell(supports)
        supports = [supports{:}];
      end
      comtraj = mxDeserialize(msg_data.comtraj);
      zmptraj = mxDeserialize(msg_data.zmptraj);
      link_constraints = mxDeserialize(msg_data.link_constraints);
      qtraj = mxDeserialize(msg_data.qtraj);

      V = struct('S', S, 's1', s1, 's2', s2);
      obj = WalkingControllerData(V, msg_data.support_times,...
                                     supports, comtraj, msg_data.mu, ...
                                     msg_data.t_offset,...
                                     link_constraints, zmptraj, qtraj,...
                                     msg_data.ignore_terrain);
%       obj.s1dot = s1dot;
%       obj.s2dot = s2dot;
    end
  end
end

