
%% Setup lcm

lc = lcm.lcm.LCM.getSingleton();
aggregator = lcm.lcm.MessageAggregator();
lc.subscribe('INS_ESTIMATE', aggregator);


%% Prepare IMU data


iterations = 10000;


param.gravity = 0;%9.81; % this is in the forward left up coordinate frame
param.dt = 1E-3;
    
traj = gen_traj(iterations, param,1);

% This is what we have in the traj structure
%
% traj.iterations
% traj.utime
% traj.dt
% traj.parameters.gravity
% traj.true.P_l
% traj.true.V_l
% traj.true.f_l
% traj.true.f_b
% traj.true.a_l
% traj.true.a_b
% traj.true.w_l = w_l;
% traj.true.w_b = w_b;
% traj.true.E
% traj.true.q

%%

data{iterations} = [];

% for n = 1:iterations
%     data{n}.true.utime = traj.utime(n);
%     data{n}.true.inertial.ddp = GM_accels(n,:)';
%     data{n}.true.inertial.da = GM_rates(n,:)';
%     data{n}.true.environment.gravity = [0;0;9.81];% using forward-left-up/xyz body frame
    % add earth rate here
    % add magnetics here
% end
    

%% Send and IMU messages

% the initial conditions for the system
% pose.utime = 0;
% pose.P = zeros(3,1);
% pose.V = zeros(3,1);
% pose.R = eye(3);
% pose.f_l = zeros(3,1);

% Set initial pose states to zero
pose = init_pose();

posterior.x = zeros(15,1);
posterior.P = 1*eye(15);

for n = 1:iterations
%     disp(['imu.msg.utime= ' num2str(data{n}.true.utime)])
    
    data{n}.true.utime = traj.utime(n);
    
    data{n}.true.pose.utime = traj.utime(n);
    data{n}.true.pose.P = traj.true.P_l(n,:)';
    data{n}.true.pose.V = traj.true.V_l(n,:)';
    data{n}.true.pose.f_l = traj.true.f_l(n,:)';
    data{n}.true.pose.R = q2R(traj.true.q(n,:)');
    

    % Start without rotation information -- build up to rotations and
    % gravity components
    data{n}.true.inertial.utime = traj.utime(n);
    data{n}.true.inertial.ddp = traj.true.f_b(n,:)';
    data{n}.true.inertial.da = traj.true.w_b(n,:)';
    data{n}.true.inertial.q = traj.true.q(n,:)';
    data{n}.true.environment.gravity = 0.*[0;0;traj.parameters.gravity];% using forward-left-up/xyz body frame
   

    % Compute the truth trajectory
    if (n==1)
        % start with the correct initial conditions (first iteration -- init conditions are kept in pose)
        data{n}.trueINS.pose = INS_Mechanisation(pose, data{n}.true.inertial);
       
    else
        % normal operation
%         data{n-1}.trueINS.pose.R = data{n-1}.true.pose.R';
%         data{n}.trueINS.pose = ground_truth(traj.utime(n), data{n-1}.trueINS.pose, data{n}.true.inertial);
       data{n}.trueINS.pose = INS_Mechanisation(data{n-1}.trueINS.pose, data{n}.true.inertial);
       
    end
    
    % add earth bound effects, including gravity
    data{n}.measured.imu.utime = data{n}.true.utime;
    data{n}.measured.imu.gyr = data{n}.true.inertial.da;
    data{n}.measured.imu.acc = data{n}.true.inertial.ddp + 0.*data{n}.trueINS.pose.R'*data{n}.true.environment.gravity;
    data{n}.measured.imu.q = data{n}.true.inertial.q;
    
    % Add sensor errors
%     data{n}.measured.imu.gyr = data{n}.measured.imu.gyr + [0;10/3600*pi/180;0];
    
    % send the simulated IMU measurements via LCM
    sendimu(data{n}.measured,lc);
%     pause(0.001);
     
    data{n}.INS.pose = receivepose(aggregator);
    
    % here we will start looking at the data fusion task. 
    % this can also live in a separate MATLAB instance via LCM to aid
    % the development cycle
    
    Measurement.INS.Pose = data{n}.INS.pose;
    Measurement.LegOdo.Pose = data{n}.true.pose;
%     
    Sys.T = 0.001;% this should be taken from the utime stamps when ported to real data
    Sys.posterior = posterior;
    
    [Result, data{n}.df] = iterate([], Sys, Measurement);
    
    posterior = data{n}.df.posterior;
    
end

disp('Out of loop')

% return


%% plot some stuff

start = 1;
stop = iterations;

t = lookatvector(data,start,stop,'true.utime').*1e-6;

figure(1), clf;
plot3(lookatvector(data,start,stop,'true.pose.P(1)'),lookatvector(data,start,stop,'true.pose.P(2)'),lookatvector(data,start,stop,'true.pose.P(3)'))
hold on
plot3(lookatvector(data,start,stop,'INS.pose.P(1)'),lookatvector(data,start,stop,'INS.pose.P(2)'),lookatvector(data,start,stop,'INS.pose.P(3)'),'r')
grid on
title(['3D Position from ' num2str(t(1)) ' s to ' num2str(t(end)) ' s'])
axis equal

%%
if (false)
    
figure(2),clf;
plot(t,lookatvector(data,start,stop,'true.pose.V(1)'),t,lookatvector(data,start,stop,'true.pose.V(2)'),t,lookatvector(data,start,stop,'true.pose.V(3)'))
grid on
title('Velocity components')
xlabel('Time [s]')
ylabel('[m/s]')
legend({'X';'Y';'Z'})

figure(3), clf;
subplot(3,3,1)
errPx = (lookatvector(data,start,stop,'true.pose.P(1)')-lookatvector(data,start,stop,'INS.pose.P(1)'));
errPy = (lookatvector(data,start,stop,'true.pose.P(2)')-lookatvector(data,start,stop,'INS.pose.P(2)'));
errPz = (lookatvector(data,start,stop,'true.pose.P(3)')-lookatvector(data,start,stop,'INS.pose.P(3)'));

% plot(t, sqrt(errPx.^2 + errPy.^2 + errPz.^2));
plot(t, errPx, t, errPy, t, errPz);
grid on
title('Position Error')
xlabel('Time [s]')

subplot(3,3,2)
estPx = (lookatvector(data,start,stop,'df.posterior.x(1)'));
estPy = (lookatvector(data,start,stop,'df.posterior.x(2)'));
estPz = (lookatvector(data,start,stop,'df.posterior.x(3)'));

plot(t, estPx, t, estPy, t, estPz);
title('Est P error')
grid on
xlabel('Time [s]')

end

%%
figure(4), clf
errPx = (lookatvector(data,start,stop,'true.pose.P(1)')-lookatvector(data,start,stop,'trueINS.pose.P(1)'));
errPy = (lookatvector(data,start,stop,'true.pose.P(2)')-lookatvector(data,start,stop,'trueINS.pose.P(2)'));
errPz = (lookatvector(data,start,stop,'true.pose.P(3)')-lookatvector(data,start,stop,'trueINS.pose.P(3)'));

subplot(311)
plot(t, errPx, t, errPy, t, errPz);
title('Local true INS P residual')



errVx = (lookatvector(data,start,stop,'true.pose.V(1)')-lookatvector(data,start,stop,'trueINS.pose.V(1)'));
errVy = (lookatvector(data,start,stop,'true.pose.V(2)')-lookatvector(data,start,stop,'trueINS.pose.V(2)'));
errVz = (lookatvector(data,start,stop,'true.pose.V(3)')-lookatvector(data,start,stop,'trueINS.pose.V(3)'));

subplot(312)
plot(t, errVx, t, errVy, t, errVz);
title('Local true INS V residual')

errAx = (lookatvector(data,start,stop,'true.pose.f_l(1)')-lookatvector(data,start,stop,'trueINS.pose.f_l(1)'));
errAy = (lookatvector(data,start,stop,'true.pose.f_l(2)')-lookatvector(data,start,stop,'trueINS.pose.f_l(2)'));
errAz = (lookatvector(data,start,stop,'true.pose.f_l(3)')-lookatvector(data,start,stop,'trueINS.pose.f_l(3)'));

subplot(313)
plot(t, errAx, t, errAy, t, errAz);
title('Local true INS accel residual')