function [F,L,Q] = dINS_EKFmodel(pose)



F = zeros(15);
F(1:3,4:6) = -q2R(qconj(pose.lQb));
F(7:9,1:3) = -vec2skew(pose.a_l);
F(7:9,10:12) = -q2R(qconj(pose.lQb));
F(13:15,7:9) = eye(3);

Q = 1*diag([0*1E-16*ones(1,3), 1E-5*ones(1,3), 0*1E-15*ones(1,3), 1E-6*ones(1,3), 0*ones(1,3)]);

% uneasy about the negative signs, but this is what we have in
% literature (noise is noise, right.)
L = blkdiag(eye(3), -eye(3), -q2R(qconj(pose.lQb)), eye(3), eye(3));


return
%%

F = zeros(15);
F(1:3,4:6) = -q2R(qconj(plQb));
F(7:9,1:3) = -vec2skew(predicted.al(k,:)');
F(7:9,10:12) = -q2R(qconj(plQb));
F(13:15,7:9) = eye(3);

covariances.R = diag([9E-1*ones(3,1)]);
Q = 1*diag([0*1E-16*ones(1,3), 1E-5*ones(1,3), 0*1E-15*ones(1,3), 1E-6*ones(1,3), 0*ones(1,3)]);

% uneasy about the negative signs, but this is what we have in
% literature (noise is noise, right.)
L = blkdiag(eye(3), -eye(3), -q2R(qconj(plQb)), eye(3), eye(3));



