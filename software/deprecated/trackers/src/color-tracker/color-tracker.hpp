#ifndef COLOR_THRESHOLD_
#define COLOR_THRESHOLD_

#include <boost/shared_ptr.hpp>
#include <lcm/lcm-cpp.hpp>

#include <opencv2/opencv.hpp>
#include <opencv2/highgui/highgui.hpp>
#include <pronto_utils/pronto_vis.hpp> // visualize pt clds
#include <image_io_utils/image_io_utils.hpp> // to simplify jpeg/zlib compression and decompression

class ColorTracker
{
  public:
    typedef boost::shared_ptr<ColorTracker> Ptr;
    typedef boost::shared_ptr<const ColorTracker> ConstPtr;
        
    ColorTracker (boost::shared_ptr<lcm::LCM> &lcm_, 
                               int width_, int height_, 
                               double fx_, double fy_, double cx_, double cy_);

    ~ColorTracker() {
    }

    //std::vector<float> ColorTracker(pcl::PointCloud<pcl::PointXYZRGB>::Ptr pts, uint8_t* img_data,
    std::vector<float> doColorTracker(std::vector< Eigen::Vector3d > & pts, uint8_t* img_data,
                                        Eigen::Isometry3d local_to_camera, int64_t current_utime);
    
    IplImage* GetThresholdedImage(IplImage* img);
    
  private:
    boost::shared_ptr<lcm::LCM> lcm_;
    pronto_vis* pc_vis_;
    int mode_;

    int width_;
    int height_;
    double fx_, fy_, cx_, cy_;    

    image_io_utils*  imgutils_;  
};




#endif